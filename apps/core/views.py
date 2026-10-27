"""
BudgetTrack – Personal Budget Allowance System
Core views.

Navigation: Dashboard | My Allowance | Expenses | Goals | List | Profile
- Suggestions/Budget Advisor  → integrated into My Allowance
- Spending Summary / Analytics → integrated into Dashboard
"""
import datetime
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction as db_transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from apps.core.models import (
    Allowance,
    AllowanceBudget,
    BudgetPlan,
    Category,
    Expense,
    ExpenseItem,
    GoalContribution,
    GroceryItem,
    Income,
    RecurringExpense,
    SavedSuggestion,
    SavingsGoal,
    Transaction,
)

User = get_user_model()

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION = timedelta(minutes=1)
INVALID_LOGIN_MESSAGE = 'Invalid username or password.'
LOCKED_MESSAGE = 'Too many failed attempts. Locked for 1 minute.'

# Budget category labels used across views & templates
BUDGET_CAT_LABELS = {
    'food': 'Food',
    'transportation': 'Transportation',
    'school_expenses': 'School Expenses',
    'personal_needs': 'Personal Needs',
    'savings': 'Savings',
    'emergency_fund': 'Emergency Fund',
    'other': 'Other',
}

# ExpenseItem category → AllowanceBudget field mapping
EXPENSE_TO_BUDGET_CAT = {
    'food': 'food',
    'transportation': 'transportation',
    'school_supplies': 'school_expenses',
    'personal_needs': 'personal_needs',
    'other': 'other',
}

# Default percentage splits per period
DEFAULT_SPLITS = {
    'weekly': {
        'food': Decimal('0.30'),
        'transportation': Decimal('0.25'),
        'school_expenses': Decimal('0.15'),
        'personal_needs': Decimal('0.05'),
        'savings': Decimal('0.15'),
        'emergency_fund': Decimal('0.10'),
        'other': Decimal('0.00'),
    },
    'daily': {
        'food': Decimal('0.40'),
        'transportation': Decimal('0.30'),
        'school_expenses': Decimal('0.10'),
        'personal_needs': Decimal('0.05'),
        'savings': Decimal('0.10'),
        'emergency_fund': Decimal('0.05'),
        'other': Decimal('0.00'),
    },
    'monthly': {
        'food': Decimal('0.30'),
        'transportation': Decimal('0.20'),
        'school_expenses': Decimal('0.15'),
        'personal_needs': Decimal('0.05'),
        'savings': Decimal('0.20'),
        'emergency_fund': Decimal('0.10'),
        'other': Decimal('0.00'),
    },
}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _posted_pk(value):
    value = str(value or '').strip()
    return int(value) if value.isdigit() else None


def _to_decimal(value, default=None):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _to_date(value):
    try:
        return datetime.datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _to_month(value):
    for fmt in ('%Y-%m', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(value, fmt).date().replace(day=1)
        except (TypeError, ValueError):
            continue
    return None


def _scoped_or_none(model, user, pk):
    pk = _posted_pk(pk)
    if pk is None:
        return None
    return model.objects.filter(id=pk, user=user).first()


def _user_category(user, value):
    pk = _posted_pk(value)
    if pk is None:
        return None
    return Category.objects.filter(pk=pk, user=user).first()


# ---------------------------------------------------------------------------
# Auth lock helpers
# ---------------------------------------------------------------------------

def _reset_lock_state(request):
    request.session['failed_attempts'] = 0
    request.session['lock_time'] = None


def _get_lock_state(request):
    failed_attempts = request.session.get('failed_attempts', 0)
    lock_time = request.session.get('lock_time')
    if not lock_time:
        return failed_attempts, None
    try:
        unlock_time = timezone.datetime.fromisoformat(lock_time)
    except (TypeError, ValueError):
        _reset_lock_state(request)
        return 0, None
    if timezone.now() < unlock_time:
        remaining = int((unlock_time - timezone.now()).total_seconds())
        return failed_attempts, remaining
    _reset_lock_state(request)
    return 0, None


def _set_lock_state(request):
    lock_until = timezone.now() + LOCK_DURATION
    request.session['failed_attempts'] = MAX_FAILED_ATTEMPTS
    request.session['lock_time'] = lock_until.isoformat()
    return int(LOCK_DURATION.total_seconds())


def _validate_registration_data(data):
    errors = {}
    username = data.get('username', '').strip()
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')

    if not username:
        errors['username'] = 'Username is required.'
    elif User.objects.filter(username=username).exists():
        errors['username'] = 'Username already taken.'
    if not first_name:
        errors['first_name'] = 'First name is required.'
    if not last_name:
        errors['last_name'] = 'Last name is required.'
    if not email:
        errors['email'] = 'Email is required.'
    else:
        try:
            validate_email(email)
        except ValidationError:
            errors['email'] = 'Enter a valid email address.'
        else:
            if User.objects.filter(email=email).exists():
                errors['email'] = 'Email already registered.'
    if not password:
        errors['password'] = 'Password is required.'
    elif len(password) < 8:
        errors['password'] = 'Password must be at least 8 characters.'
    else:
        try:
            validate_password(password)
        except ValidationError as exc:
            errors['password'] = ' '.join(exc.messages)
    if password != confirm_password:
        errors['confirm_password'] = 'Passwords do not match.'
    return errors


# ---------------------------------------------------------------------------
# Financial calculation helpers (single source of truth)
# ---------------------------------------------------------------------------

def _compute_balance(user):
    """
    Available balance = total allowance − purchased expense items − goal contributions.
    This is the single authoritative balance formula used everywhere.
    """
    total_allowance = (
        Allowance.objects.filter(user=user).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )
    total_purchased = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )
    total_contributions = (
        GoalContribution.objects.filter(user=user)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )
    return total_allowance - total_purchased - total_contributions


def _record_transaction(user, transaction_type, description, category, amount, date):
    """Write an immutable ledger entry. amount: positive = in, negative = out."""
    current_balance = _compute_balance(user)
    Transaction.objects.create(
        user=user,
        transaction_type=transaction_type,
        description=description,
        category=category,
        amount=amount,
        balance_after=current_balance,
        date=date,
    )


def _period_dates(period, ref_date=None):
    """Return (start, end) for a given period relative to ref_date."""
    today = ref_date or timezone.now().date()
    if period == 'daily':
        return today, today
    if period == 'weekly':
        start = today - timedelta(today.weekday())
        return start, start + timedelta(days=6)
    # monthly
    start = today.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
    return start, end


def _spending_by_category(user, start_date, end_date):
    """
    Return a dict {category_key: amount_spent} for purchased ExpenseItems
    in the given date range. Uses EXPENSE_TO_BUDGET_CAT mapping.
    """
    items = ExpenseItem.objects.filter(
        user=user,
        balance_deducted=True,
        date__gte=start_date,
        date__lte=end_date,
    )
    result = {k: Decimal('0') for k in BUDGET_CAT_LABELS}
    for item in items:
        bcat = EXPENSE_TO_BUDGET_CAT.get(item.category, 'other')
        result[bcat] = result.get(bcat, Decimal('0')) + item.total_cost
    return result


def _build_budget_tracking(budget, user):
    """
    Given an AllowanceBudget, compute planned vs actual per category.
    Returns list of dicts for template rendering.
    """
    spending = _spending_by_category(user, budget.period_start, budget.period_end)
    rows = []
    for cat_key, label in BUDGET_CAT_LABELS.items():
        planned = getattr(budget, cat_key, Decimal('0'))
        spent = spending.get(cat_key, Decimal('0'))
        remaining = max(Decimal('0'), planned - spent)
        over_budget = spent > planned and planned > 0
        pct = min(100, int((spent / planned * 100))) if planned > 0 else 0
        rows.append({
            'key': cat_key,
            'label': label,
            'planned': planned,
            'spent': spent,
            'remaining': remaining,
            'over_budget': over_budget,
            'pct': pct,
        })
    return rows


def _suggest_allocations(amount, period):
    """Return suggested allocation dict for a given amount and period."""
    splits = DEFAULT_SPLITS.get(period, DEFAULT_SPLITS['weekly'])
    result = {}
    assigned = Decimal('0')
    keys = list(splits.keys())
    for i, cat in enumerate(keys):
        if i == len(keys) - 1:
            # Give remainder to last category to avoid rounding drift
            result[cat] = max(Decimal('0'), amount - assigned)
        else:
            val = (amount * splits[cat]).quantize(Decimal('1'))
            result[cat] = val
            assigned += val
    return result


# ---------------------------------------------------------------------------
# Auth views
# ---------------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    failed_attempts, remaining = _get_lock_state(request)
    if remaining is not None:
        messages.error(request, f'Too many failed attempts. Try again in {remaining} seconds.')
        return render(request, 'auth/login.html', {'locked': True, 'remaining': remaining})
    if request.method == 'GET':
        return render(request, 'auth/login.html')

    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '')
    user = authenticate(request, username=username, password=password)
    if user is not None:
        _reset_lock_state(request)
        login(request, user)
        return redirect('dashboard')

    failed_attempts += 1
    request.session['failed_attempts'] = failed_attempts
    if failed_attempts >= MAX_FAILED_ATTEMPTS:
        remaining = _set_lock_state(request)
        messages.error(request, LOCKED_MESSAGE)
        return render(request, 'auth/login.html', {'locked': True, 'remaining': remaining})
    remaining_attempts = MAX_FAILED_ATTEMPTS - failed_attempts
    messages.error(request, f'{INVALID_LOGIN_MESSAGE} {remaining_attempts} attempts remaining.')
    return render(request, 'auth/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


@require_http_methods(['GET', 'POST'])
def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'GET':
        return render(request, 'auth/register.html')
    form_data = {
        'username': request.POST.get('username', ''),
        'first_name': request.POST.get('first_name', ''),
        'last_name': request.POST.get('last_name', ''),
        'email': request.POST.get('email', ''),
        'password': request.POST.get('password', ''),
        'confirm_password': request.POST.get('confirm_password', ''),
    }
    errors = _validate_registration_data(form_data)
    if errors:
        return render(request, 'auth/register.html', {'errors': errors, 'form_data': form_data})
    user = User.objects.create_user(
        username=form_data['username'].strip(),
        first_name=form_data['first_name'].strip(),
        last_name=form_data['last_name'].strip(),
        email=form_data['email'].strip(),
        password=form_data['password'],
    )
    login(request, user)
    return redirect('dashboard')


# ---------------------------------------------------------------------------
# Dashboard – Spending Summary + Budget Analytics
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def home(request):
    user = request.user
    today = timezone.now().date()

    # Period selection via GET param
    view_period = request.GET.get('period', 'weekly')
    if view_period not in ('daily', 'weekly', 'monthly'):
        view_period = 'weekly'
    period_start, period_end = _period_dates(view_period, today)

    # Core totals
    total_allowance = (
        Allowance.objects.filter(user=user).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )
    total_purchased = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )
    total_savings = (
        GoalContribution.objects.filter(user=user)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )
    remaining_balance = total_allowance - total_purchased - total_savings

    # Period-specific spending
    period_spending = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True,
                                   date__gte=period_start, date__lte=period_end)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )
    today_spending = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True, date=today)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )

    # Daily / weekly / monthly spending for all three (for the 3-card summary)
    daily_start, daily_end = _period_dates('daily', today)
    weekly_start, weekly_end = _period_dates('weekly', today)
    monthly_start, monthly_end = _period_dates('monthly', today)

    daily_spending = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True,
                                   date__gte=daily_start, date__lte=daily_end)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )
    weekly_spending = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True,
                                   date__gte=weekly_start, date__lte=weekly_end)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )
    monthly_spending = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True,
                                   date__gte=monthly_start, date__lte=monthly_end)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )

    # Category breakdown for selected period
    cat_spending = _spending_by_category(user, period_start, period_end)

    # Find the active budget plan for the current period (if any)
    active_budget = (
        AllowanceBudget.objects.filter(
            user=user,
            period=view_period,
            period_start__lte=today,
            period_end__gte=today,
        ).order_by('-created_at').first()
    )
    budget_tracking = _build_budget_tracking(active_budget, user) if active_budget else []

    # Budget warnings (> 80% spent)
    warnings = []
    if active_budget:
        for row in budget_tracking:
            if row['planned'] > 0 and row['pct'] >= 80:
                label = 'exceeded' if row['over_budget'] else 'almost at limit'
                warnings.append(
                    f'{row["label"]} budget is {label} (₱{row["spent"]:.2f} / ₱{row["planned"]:.2f})'
                )

    # Goals
    savings_goals = SavingsGoal.objects.filter(user=user)
    goals_count = savings_goals.count()
    total_goal_saved = savings_goals.aggregate(t=Sum('current_amount'))['t'] or Decimal('0')

    recent_transactions = Transaction.objects.filter(user=user).order_by('-date', '-created_at')[:10]

    context = {
        'total_allowance': total_allowance,
        'total_expenses': total_purchased,
        'total_savings': total_savings,
        'remaining_balance': remaining_balance,
        'today_spending': today_spending,
        'daily_spending': daily_spending,
        'weekly_spending': weekly_spending,
        'monthly_spending': monthly_spending,
        'period_spending': period_spending,
        'period_start': period_start,
        'period_end': period_end,
        'view_period': view_period,
        'cat_spending': cat_spending,
        'cat_labels': BUDGET_CAT_LABELS,
        'active_budget': active_budget,
        'budget_tracking': budget_tracking,
        'warnings': warnings,
        'goals_count': goals_count,
        'total_goal_saved': total_goal_saved,
        'savings_goals': savings_goals,
        'recent_transactions': recent_transactions,
        'today': today,
        'weekly_start': weekly_start,
        'weekly_end': weekly_end,
        'monthly_start': monthly_start,
        'monthly_end': monthly_end,
    }
    return render(request, 'home.html', context)


# ---------------------------------------------------------------------------
# My Allowance – with integrated Smart Budget Planner
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def my_allowance(request):
    user = request.user
    today = timezone.now().date()
    allowances = user.allowances.order_by('-date_received')
    budget_plans = AllowanceBudget.objects.filter(user=user).order_by('-period_start')

    if request.method == 'POST':
        action = request.POST.get('action')

        # ── Add allowance ──────────────────────────────────────────────────
        if action == 'add_allowance':
            amount = _to_decimal(request.POST.get('amount'))
            if amount is None or amount <= 0:
                messages.error(request, 'Amount must be greater than 0.')
                return redirect('my_allowance')
            date = _to_date(request.POST.get('date_received'))
            if date is None:
                messages.error(request, 'A valid date is required.')
                return redirect('my_allowance')
            with db_transaction.atomic():
                allowance = Allowance.objects.create(
                    name=request.POST.get('name', 'Weekly Allowance'),
                    amount=amount,
                    allowance_type=request.POST.get('allowance_type', 'weekly'),
                    date_received=date,
                    description=request.POST.get('description', ''),
                    user=user,
                )
                _record_transaction(
                    user=user,
                    transaction_type='allowance_added',
                    description=allowance.name,
                    category='Allowance',
                    amount=amount,
                    date=date,
                )
            messages.success(request, f'Allowance of ₱{amount:.2f} added.')
            return redirect('my_allowance')

        # ── Save / update budget plan ──────────────────────────────────────
        elif action == 'save_budget':
            period = request.POST.get('budget_period', 'weekly')
            if period not in ('daily', 'weekly', 'monthly'):
                period = 'weekly'

            total_amount = _to_decimal(request.POST.get('budget_total_amount'))
            if total_amount is None or total_amount <= 0:
                messages.error(request, 'Budget amount must be greater than 0.')
                return redirect('my_allowance')

            period_start = _to_date(request.POST.get('budget_period_start'))
            if period_start is None:
                messages.error(request, 'Budget start date is required.')
                return redirect('my_allowance')

            # Auto-compute end date
            if period == 'daily':
                period_end = period_start
            elif period == 'weekly':
                period_end = period_start + timedelta(days=6)
            else:  # monthly
                if period_start.month == 12:
                    period_end = period_start.replace(year=period_start.year + 1, month=1, day=1) - timedelta(days=1)
                else:
                    period_end = period_start.replace(month=period_start.month + 1, day=1) - timedelta(days=1)

            # Read category allocations
            cats = {}
            total_allocated = Decimal('0')
            for cat in AllowanceBudget.BUDGET_CATEGORIES:
                val = _to_decimal(request.POST.get(f'alloc_{cat}'), Decimal('0'))
                cats[cat] = max(Decimal('0'), val)
                total_allocated += cats[cat]

            if total_allocated > total_amount:
                messages.error(
                    request,
                    f'Total allocation (₱{total_allocated:.2f}) exceeds the budget amount (₱{total_amount:.2f}).'
                )
                return redirect('my_allowance')

            # Link to allowance if provided
            allowance_id = _posted_pk(request.POST.get('budget_allowance_id'))
            linked_allowance = None
            if allowance_id:
                linked_allowance = Allowance.objects.filter(id=allowance_id, user=user).first()

            # Check if a plan already exists for this period+period_start; update it
            budget_pk = _posted_pk(request.POST.get('budget_id'))
            if budget_pk:
                bp = AllowanceBudget.objects.filter(id=budget_pk, user=user).first()
            else:
                bp = AllowanceBudget.objects.filter(
                    user=user, period=period, period_start=period_start
                ).first()

            if bp:
                # Update existing
                bp.total_amount = total_amount
                bp.period_end = period_end
                bp.allowance = linked_allowance
                bp.notes = request.POST.get('budget_notes', '')
                for cat in AllowanceBudget.BUDGET_CATEGORIES:
                    setattr(bp, cat, cats[cat])
                bp.save()
                messages.success(request, 'Budget plan updated. No money was deducted.')
            else:
                AllowanceBudget.objects.create(
                    user=user,
                    period=period,
                    period_start=period_start,
                    period_end=period_end,
                    total_amount=total_amount,
                    allowance=linked_allowance,
                    notes=request.POST.get('budget_notes', ''),
                    **cats,
                )
                messages.success(request, 'Budget plan saved. No money was deducted.')
            return redirect('my_allowance')

        # ── Delete budget plan ─────────────────────────────────────────────
        elif action == 'delete_budget':
            budget_pk = _posted_pk(request.POST.get('budget_id'))
            bp = AllowanceBudget.objects.filter(id=budget_pk, user=user).first()
            if bp:
                bp.delete()
                messages.success(request, 'Budget plan deleted.')
            return redirect('my_allowance')

    # ── GET: build context ─────────────────────────────────────────────────
    weekly_start, weekly_end = _period_dates('weekly', today)
    weekly_allowance = (
        allowances.filter(date_received__gte=weekly_start, date_received__lte=weekly_end)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )
    balance = _compute_balance(user)

    # Active plan for current week (for budget tracking display)
    active_plan = (
        AllowanceBudget.objects.filter(
            user=user, period_start__lte=today, period_end__gte=today
        ).order_by('-created_at').first()
    )
    budget_tracking = _build_budget_tracking(active_plan, user) if active_plan else []

    # Pre-compute daily breakdown for active plan
    daily_equiv = None
    if active_plan and active_plan.period != 'daily':
        daily_equiv = active_plan.daily_amount()

    # Budget plan list with tracking data
    plans_with_tracking = []
    for bp in budget_plans:
        tracking = _build_budget_tracking(bp, user)
        total_spent = sum(row['spent'] for row in tracking)
        plans_with_tracking.append({
            'plan': bp,
            'tracking': tracking,
            'total_spent': total_spent,
        })

    # Active plan total spent
    active_plan_total_spent = Decimal('0')
    if active_plan:
        for row in budget_tracking:
            active_plan_total_spent += row['spent']

    context = {
        'allowances': allowances,
        'weekly_allowance': weekly_allowance,
        'today': today,
        'weekly_start': weekly_start,
        'weekly_end': weekly_end,
        'balance': balance,
        'active_plan': active_plan,
        'budget_tracking': budget_tracking,
        'daily_equiv': daily_equiv,
        'active_plan_total_spent': active_plan_total_spent,
        'budget_plans': budget_plans,
        'plans_with_tracking': plans_with_tracking,
        'budget_cat_labels': BUDGET_CAT_LABELS,
        'budget_categories': AllowanceBudget.BUDGET_CATEGORIES,
    }
    return render(request, 'my_allowance.html', context)


# ---------------------------------------------------------------------------
# AJAX: Budget suggestions (advisor) – used from My Allowance page
# ---------------------------------------------------------------------------

@login_required(login_url='login')
@require_POST
def budget_suggest_ajax(request):
    """Return suggested allocations for a given amount + period as JSON."""
    amount = _to_decimal(request.POST.get('amount'))
    period = request.POST.get('period', 'weekly')
    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Enter an amount greater than 0.'}, status=400)
    if period not in DEFAULT_SPLITS:
        period = 'weekly'

    suggestions = _suggest_allocations(amount, period)
    result = {
        cat: float(val) for cat, val in suggestions.items()
    }
    result['total'] = float(amount)
    result['period'] = period
    result['labels'] = BUDGET_CAT_LABELS
    return JsonResponse(result)


# ---------------------------------------------------------------------------
# AJAX: Allowance save / delete
# ---------------------------------------------------------------------------

@login_required(login_url='login')
@require_POST
def allowance_save_ajax(request):
    pk = request.POST.get('id')
    is_update = bool(pk)
    if is_update:
        allowance = _scoped_or_none(Allowance, request.user, pk)
        if allowance is None:
            return JsonResponse({'error': 'Allowance not found.'}, status=404)
    else:
        allowance = Allowance()

    amount = _to_decimal(request.POST.get('amount'))
    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Amount must be greater than 0.'}, status=400)
    date = _to_date(request.POST.get('date_received'))
    if date is None:
        return JsonResponse({'error': 'A valid date is required.'}, status=400)

    allowance.name = request.POST.get('name', 'Weekly Allowance')
    allowance.amount = amount
    allowance.allowance_type = request.POST.get('allowance_type', 'weekly')
    allowance.date_received = date
    allowance.description = request.POST.get('description', '').strip()
    if not allowance.pk:
        allowance.user = request.user
    with db_transaction.atomic():
        allowance.save()
        if not is_update:
            _record_transaction(
                user=request.user,
                transaction_type='allowance_added',
                description=allowance.name,
                category='Allowance',
                amount=amount,
                date=date,
            )
    messages.success(request, f'Allowance has been {"updated" if is_update else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': allowance.id})


@login_required(login_url='login')
@require_POST
def allowance_delete_ajax(request, pk):
    allowance = _scoped_or_none(Allowance, request.user, pk)
    if allowance is None:
        return JsonResponse({'error': 'Allowance not found.'}, status=404)
    allowance.delete()
    messages.success(request, 'Allowance has been deleted successfully.')
    return JsonResponse({'status': 'deleted'})


# ---------------------------------------------------------------------------
# Expense Items
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def expense_items_page(request):
    user = request.user
    expense_items = ExpenseItem.objects.filter(user=user).order_by('-date', '-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_expense_item':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Item name is required.')
                return redirect('expense_items')
            category = request.POST.get('category', 'other')
            quantity = _to_decimal(request.POST.get('quantity', '1'))
            price = _to_decimal(request.POST.get('price_per_item'))
            date = _to_date(request.POST.get('date'))
            if quantity is None or quantity < 1:
                messages.error(request, 'Quantity must be at least 1.')
                return redirect('expense_items')
            if price is None or price <= 0:
                messages.error(request, 'Price per item must be greater than 0.')
                return redirect('expense_items')
            if date is None:
                messages.error(request, 'A valid date is required.')
                return redirect('expense_items')
            quantity = int(quantity)
            total_cost = Decimal(str(quantity)) * price
            ExpenseItem.objects.create(
                name=name, category=category, quantity=quantity,
                price_per_item=price, total_cost=total_cost,
                status='planned', date=date, balance_deducted=False, user=user,
            )
            messages.success(request, f'{name} added to your expense list.')
            return redirect('expense_items')

        elif action == 'mark_purchased':
            item_pk = _posted_pk(request.POST.get('item_id'))
            item = ExpenseItem.objects.filter(id=item_pk, user=user).first() if item_pk else None
            if item is None:
                messages.error(request, 'Expense item not found.')
                return redirect('expense_items')
            if item.balance_deducted:
                messages.warning(request, 'That item has already been marked as purchased.')
                return redirect('expense_items')
            balance = _compute_balance(user)
            if item.total_cost > balance:
                messages.error(
                    request,
                    f'Insufficient balance. You have ₱{balance:.2f} available but this item costs ₱{item.total_cost:.2f}.'
                )
                return redirect('expense_items')
            with db_transaction.atomic():
                item.status = 'purchased'
                item.balance_deducted = True
                item.save()
                _record_transaction(
                    user=user, transaction_type='purchase',
                    description=item.name, category=item.get_category_display(),
                    amount=-item.total_cost, date=item.date,
                )
            messages.success(request, f'{item.name} marked as purchased. ₱{item.total_cost:.2f} deducted.')
            return redirect('expense_items')

        elif action == 'cancel_item':
            item_pk = _posted_pk(request.POST.get('item_id'))
            item = ExpenseItem.objects.filter(id=item_pk, user=user).first() if item_pk else None
            if item is None:
                messages.error(request, 'Expense item not found.')
                return redirect('expense_items')
            if item.status == 'purchased' and item.balance_deducted:
                with db_transaction.atomic():
                    item.status = 'cancelled'
                    item.balance_deducted = False
                    item.save()
                    _record_transaction(
                        user=user, transaction_type='refund',
                        description=f'Refund: {item.name}',
                        category=item.get_category_display(),
                        amount=item.total_cost, date=timezone.now().date(),
                    )
                messages.success(request, f'Purchase of {item.name} refunded. ₱{item.total_cost:.2f} returned.')
            else:
                item.status = 'cancelled'
                item.save()
                messages.success(request, f'{item.name} cancelled.')
            return redirect('expense_items')

        elif action == 'delete_item':
            item_pk = _posted_pk(request.POST.get('item_id'))
            item = ExpenseItem.objects.filter(id=item_pk, user=user).first() if item_pk else None
            if item is None:
                messages.error(request, 'Expense item not found.')
                return redirect('expense_items')
            if item.balance_deducted:
                messages.error(request, 'Cannot delete a purchased item. Cancel/refund it first.')
                return redirect('expense_items')
            item.delete()
            messages.success(request, 'Expense item deleted.')
            return redirect('expense_items')

    balance = _compute_balance(user)
    context = {
        'expense_items': expense_items,
        'balance': balance,
        'today': timezone.now().date(),
    }
    return render(request, 'expense_items.html', context)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def goals_page(request):
    user = request.user
    savings_goals = SavingsGoal.objects.filter(user=user).order_by('-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_goal':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Goal name is required.')
                return redirect('goals')
            target_amount = _to_decimal(request.POST.get('target_amount'))
            if target_amount is None or target_amount <= 0:
                messages.error(request, 'Target amount must be greater than 0.')
                return redirect('goals')
            deadline = request.POST.get('deadline', '').strip()
            deadline = _to_date(deadline) if deadline else None
            SavingsGoal.objects.create(
                name=name, target_amount=target_amount,
                current_amount=Decimal('0'), deadline=deadline,
                status='in_progress', user=user,
            )
            messages.success(request, f'Goal "{name}" created.')
            return redirect('goals')

        elif action == 'add_contribution':
            goal_id = _posted_pk(request.POST.get('goal_id'))
            goal = SavingsGoal.objects.filter(id=goal_id, user=user).first() if goal_id else None
            if goal is None:
                messages.error(request, 'Goal not found.')
                return redirect('goals')
            amount = _to_decimal(request.POST.get('contribution_amount'))
            if amount is None or amount <= 0:
                messages.error(request, 'Contribution amount must be greater than 0.')
                return redirect('goals')
            balance = _compute_balance(user)
            if amount > balance:
                messages.error(request, f'Insufficient balance. You have ₱{balance:.2f} available.')
                return redirect('goals')
            note = request.POST.get('note', '').strip()
            date = timezone.now().date()
            with db_transaction.atomic():
                GoalContribution.objects.create(
                    goal=goal, amount=amount, note=note, date=date, user=user,
                )
                goal.current_amount = Decimal(str(goal.current_amount)) + amount
                goal.save()
                goal.check_and_mark_completed()
                _record_transaction(
                    user=user, transaction_type='savings_transfer',
                    description=goal.name, category='Savings Goal',
                    amount=-amount, date=date,
                )
            messages.success(request, f'₱{amount:.2f} added to "{goal.name}".')
            return redirect('goals')

        elif action == 'delete_goal':
            goal_id = _posted_pk(request.POST.get('goal_id'))
            goal = SavingsGoal.objects.filter(id=goal_id, user=user).first() if goal_id else None
            if goal is None:
                messages.error(request, 'Goal not found.')
                return redirect('goals')
            contribs = GoalContribution.objects.filter(goal=goal, user=user)
            total_refund = contribs.aggregate(t=Sum('amount'))['t'] or Decimal('0')
            if total_refund > 0:
                with db_transaction.atomic():
                    contribs.delete()
                    goal.delete()
                    _record_transaction(
                        user=user, transaction_type='refund',
                        description=f'Goal deleted: {goal.name}',
                        category='Savings Goal', amount=total_refund,
                        date=timezone.now().date(),
                    )
                messages.success(request, f'Goal deleted. ₱{total_refund:.2f} returned to your balance.')
            else:
                goal.delete()
                messages.success(request, 'Goal deleted.')
            return redirect('goals')

    balance = _compute_balance(user)
    goals_with_data = []
    for goal in savings_goals:
        contribs = GoalContribution.objects.filter(goal=goal, user=user).order_by('-date', '-created_at')
        goals_with_data.append({'goal': goal, 'contributions': contribs})

    context = {
        'savings_goals': savings_goals,
        'goals_with_data': goals_with_data,
        'balance': balance,
        'today': timezone.now().date(),
    }
    return render(request, 'goals.html', context)


# ---------------------------------------------------------------------------
# Transaction List (full financial history)
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def transaction_list_page(request):
    user = request.user
    txns = Transaction.objects.filter(user=user).order_by('-date', '-created_at')

    type_filter = request.GET.get('type', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    if type_filter:
        txns = txns.filter(transaction_type=type_filter)
    if date_from:
        d = _to_date(date_from)
        if d:
            txns = txns.filter(date__gte=d)
    if date_to:
        d = _to_date(date_to)
        if d:
            txns = txns.filter(date__lte=d)

    balance = _compute_balance(user)
    context = {
        'transactions': txns,
        'balance': balance,
        'type_filter': type_filter,
        'date_from': date_from,
        'date_to': date_to,
        'transaction_types': Transaction.TYPE_CHOICES,
    }
    return render(request, 'transaction_list.html', context)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def profile_page(request):
    user = request.user
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        if email:
            user.email = email
        user.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('profile')
    return render(request, 'profile.html', {'user': user})


# ---------------------------------------------------------------------------
# Legacy redirects (old URLs still work)
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def expenses_page(request):
    return redirect('expense_items')


@login_required(login_url='login')
def savings_goals_page(request):
    return redirect('goals')


@login_required(login_url='login')
def reports_page(request):
    return redirect('transaction_list')


@login_required(login_url='login')
def budget_planner_page(request):
    return redirect('my_allowance')


@login_required(login_url='login')
def food_list_page(request):
    return redirect('dashboard')


@login_required(login_url='login')
def suggestions_page(request):
    return redirect('my_allowance')


# ---------------------------------------------------------------------------
# Legacy AJAX endpoints (preserved so existing JS still works)
# ---------------------------------------------------------------------------

@login_required(login_url='login')
@require_POST
def income_save_ajax(request):
    pk = request.POST.get('id')
    if pk:
        income = _scoped_or_none(Income, request.user, pk)
        if income is None:
            return JsonResponse({'error': 'Income not found.'}, status=404)
    else:
        income = Income()
    amount = _to_decimal(request.POST.get('amount'))
    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Amount must be greater than 0.'}, status=400)
    date = _to_date(request.POST.get('date'))
    if date is None:
        return JsonResponse({'error': 'A valid date is required.'}, status=400)
    income.amount = amount
    income.source = request.POST.get('source', 'allowance')
    income.description = request.POST.get('description', '').strip()
    income.date = date
    if not income.pk:
        income.user = request.user
    income.save()
    messages.success(request, f'Income has been {"updated" if pk else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': income.id})


@login_required(login_url='login')
@require_POST
def income_delete_ajax(request, pk):
    income = _scoped_or_none(Income, request.user, pk)
    if income is None:
        return JsonResponse({'error': 'Income not found.'}, status=404)
    income.delete()
    messages.success(request, 'Income deleted.')
    return JsonResponse({'status': 'deleted'})


@login_required(login_url='login')
@require_POST
def expense_save_ajax(request):
    pk = request.POST.get('id')
    if pk:
        expense = _scoped_or_none(Expense, request.user, pk)
        if expense is None:
            return JsonResponse({'error': 'Expense not found.'}, status=404)
    else:
        expense = Expense()
    amount = _to_decimal(request.POST.get('amount'))
    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Amount must be greater than 0.'}, status=400)
    date = _to_date(request.POST.get('date'))
    if date is None:
        return JsonResponse({'error': 'A valid date is required.'}, status=400)
    category = _scoped_or_none(Category, request.user, request.POST.get('category'))
    expense.amount = amount
    expense.description = request.POST.get('description', '').strip()
    expense.date = date
    expense.category = category
    if not expense.pk:
        expense.user = request.user
    expense.save()
    messages.success(request, f'Expense has been {"updated" if pk else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': expense.id})


@login_required(login_url='login')
@require_POST
def expense_delete_ajax(request, pk):
    expense = _scoped_or_none(Expense, request.user, pk)
    if expense is None:
        return JsonResponse({'error': 'Expense not found.'}, status=404)
    expense.delete()
    messages.success(request, 'Expense deleted.')
    return JsonResponse({'status': 'deleted'})


@login_required(login_url='login')
@require_POST
def category_save_ajax(request):
    pk = request.POST.get('id')
    if pk:
        category = _scoped_or_none(Category, request.user, pk)
        if category is None:
            return JsonResponse({'error': 'Category not found.'}, status=404)
    else:
        category = Category()
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Category name is required.'}, status=400)
    category_type = request.POST.get('category_type', 'need')
    if category_type not in [c[0] for c in Category.CATEGORY_TYPES]:
        return JsonResponse({'error': 'Invalid category type.'}, status=400)
    category.name = name
    category.category_type = category_type
    if not category.pk:
        category.user = request.user
    try:
        category.save()
    except IntegrityError:
        return JsonResponse({'error': 'You already have a category with that name.'}, status=400)
    messages.success(request, f'Category has been {"updated" if pk else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': category.id})


@login_required(login_url='login')
@require_POST
def category_delete_ajax(request, pk):
    category = _scoped_or_none(Category, request.user, pk)
    if category is None:
        return JsonResponse({'error': 'Category not found.'}, status=404)
    category.delete()
    messages.success(request, 'Category deleted.')
    return JsonResponse({'status': 'deleted'})


@login_required(login_url='login')
@require_POST
def budget_save_ajax(request):
    pk = request.POST.get('id')
    if pk:
        plan = _scoped_or_none(BudgetPlan, request.user, pk)
        if plan is None:
            return JsonResponse({'error': 'Budget plan not found.'}, status=404)
    else:
        plan = BudgetPlan()
    category = _scoped_or_none(Category, request.user, request.POST.get('category'))
    if category is None:
        return JsonResponse({'error': 'Please select a valid category.'}, status=400)
    monthly_income = _to_decimal(request.POST.get('monthly_income'))
    allocated_amount = _to_decimal(request.POST.get('allocated_amount'))
    if monthly_income is None or monthly_income < 0:
        return JsonResponse({'error': 'Monthly income must be 0 or more.'}, status=400)
    if allocated_amount is None or allocated_amount < 0:
        return JsonResponse({'error': 'Allocated amount must be 0 or more.'}, status=400)
    month = _to_month(request.POST.get('month'))
    if month is None:
        return JsonResponse({'error': 'A valid month is required.'}, status=400)
    plan.category = category
    plan.monthly_income = monthly_income
    plan.allocated_amount = allocated_amount
    plan.month = month
    if not plan.pk:
        plan.user = request.user
    try:
        plan.save()
    except IntegrityError:
        return JsonResponse({'error': 'A budget plan for that category and month already exists.'}, status=400)
    messages.success(request, f'Budget plan has been {"updated" if pk else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': plan.id})


@login_required(login_url='login')
@require_POST
def budget_delete_ajax(request, pk):
    plan = _scoped_or_none(BudgetPlan, request.user, pk)
    if plan is None:
        return JsonResponse({'error': 'Budget plan not found.'}, status=404)
    plan.delete()
    messages.success(request, 'Budget plan deleted.')
    return JsonResponse({'status': 'deleted'})


@login_required(login_url='login')
@require_POST
def savings_save_ajax(request):
    pk = request.POST.get('id')
    if pk:
        goal = _scoped_or_none(SavingsGoal, request.user, pk)
        if goal is None:
            return JsonResponse({'error': 'Savings goal not found.'}, status=404)
    else:
        goal = SavingsGoal()
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Goal name is required.'}, status=400)
    target_amount = _to_decimal(request.POST.get('target_amount'))
    current_amount = _to_decimal(request.POST.get('current_amount'), Decimal('0'))
    if target_amount is None or target_amount <= 0:
        return JsonResponse({'error': 'Target amount must be greater than 0.'}, status=400)
    if current_amount is None or current_amount < 0:
        return JsonResponse({'error': 'Current amount must be 0 or more.'}, status=400)
    deadline = request.POST.get('deadline', '').strip()
    deadline = _to_date(deadline) if deadline else None
    goal.name = name
    goal.target_amount = target_amount
    goal.current_amount = current_amount
    goal.deadline = deadline
    if not goal.pk:
        goal.user = request.user
    goal.save()
    messages.success(request, f'Savings goal has been {"updated" if pk else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': goal.id})


@login_required(login_url='login')
@require_POST
def savings_delete_ajax(request, pk):
    goal = _scoped_or_none(SavingsGoal, request.user, pk)
    if goal is None:
        return JsonResponse({'error': 'Savings goal not found.'}, status=404)
    goal.delete()
    messages.success(request, 'Savings goal deleted.')
    return JsonResponse({'status': 'deleted'})


@login_required(login_url='login')
@require_POST
def grocery_save_ajax(request):
    pk = request.POST.get('id')
    if pk:
        item = _scoped_or_none(GroceryItem, request.user, pk)
        if item is None:
            return JsonResponse({'error': 'Grocery item not found.'}, status=404)
    else:
        item = GroceryItem()
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Item name is required.'}, status=400)
    try:
        quantity = int(request.POST.get('quantity', '1'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Quantity must be a whole number.'}, status=400)
    if quantity < 1:
        return JsonResponse({'error': 'Quantity must be at least 1.'}, status=400)
    estimated_price = _to_decimal(request.POST.get('estimated_price'), Decimal('0'))
    if estimated_price is None or estimated_price < 0:
        return JsonResponse({'error': 'Estimated price must be 0 or more.'}, status=400)
    item.name = name
    item.quantity = quantity
    item.estimated_price = estimated_price
    if not item.pk:
        item.user = request.user
    item.save()
    messages.success(request, f'Grocery item has been {"updated" if pk else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': item.id})


@login_required(login_url='login')
@require_POST
def grocery_toggle_ajax(request, pk):
    item = _scoped_or_none(GroceryItem, request.user, pk)
    if item is None:
        return JsonResponse({'error': 'Grocery item not found.'}, status=404)
    item.is_purchased = not item.is_purchased
    item.save()
    return JsonResponse({'status': 'success', 'is_purchased': item.is_purchased})


@login_required(login_url='login')
@require_POST
def grocery_delete_ajax(request, pk):
    item = _scoped_or_none(GroceryItem, request.user, pk)
    if item is None:
        return JsonResponse({'error': 'Grocery item not found.'}, status=404)
    item.delete()
    messages.success(request, 'Grocery item deleted.')
    return JsonResponse({'status': 'deleted'})


# Legacy save suggestion (kept so old JS calls don't 404)
@login_required(login_url='login')
@require_POST
def save_suggestion_ajax(request):
    title = request.POST.get('title', '').strip()
    body = request.POST.get('body', '').strip()
    category = request.POST.get('category', '').strip()
    if not title:
        return JsonResponse({'error': 'Title is required.'}, status=400)
    _, created = SavedSuggestion.objects.get_or_create(
        title=title, user=request.user,
        defaults={'body': body, 'category': category},
    )
    if created:
        return JsonResponse({'status': 'saved', 'message': f'Suggestion "{title}" saved.'})
    return JsonResponse({'status': 'duplicate', 'message': f'"{title}" already saved.'})


@login_required(login_url='login')
@require_POST
def delete_suggestion_ajax(request, pk):
    suggestion = _scoped_or_none(SavedSuggestion, request.user, pk)
    if suggestion is None:
        return JsonResponse({'error': 'Not found.'}, status=404)
    suggestion.delete()
    return JsonResponse({'status': 'deleted'})


# Legacy budget_advisor_ajax — keep the old URL working
@login_required(login_url='login')
@require_POST
def budget_advisor_ajax(request):
    return budget_suggest_ajax(request)
