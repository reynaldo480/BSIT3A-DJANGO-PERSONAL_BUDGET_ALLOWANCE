"""
BudgetTrack – Personal Budget Allowance System
Core views — complete ActivityLog audit trail + safe allowance deletion.
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
    ActivityLog, Allowance, AllowanceBudget, BudgetPlan,
    Category, Expense, ExpenseItem, GoalContribution,
    GroceryItem, Income, RecurringExpense, SavedSuggestion,
    SavingsGoal, Transaction,
)

User = get_user_model()
MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION = timedelta(minutes=1)

BUDGET_CAT_LABELS = {
    'food': 'Food', 'transportation': 'Transportation',
    'school_expenses': 'School Expenses', 'personal_needs': 'Personal Needs',
    'savings': 'Savings', 'emergency_fund': 'Emergency Fund', 'other': 'Other',
}
EXPENSE_TO_BUDGET_CAT = {
    'food':            'food',
    'transportation':  'transportation',
    'school_expenses': 'school_expenses',
    'school_supplies': 'school_expenses',   # legacy backward-compat
    'personal_needs':  'personal_needs',
    'savings':         'savings',
    'emergency_fund':  'emergency_fund',
    'other':           'other',
}

# Valid expense categories — single source of truth, kept in sync with ExpenseItem.CATEGORY_CHOICES
EXPENSE_CATEGORIES = [
    ('food',            'Food'),
    ('transportation',  'Transportation'),
    ('school_expenses', 'School Expenses'),
    ('personal_needs',  'Personal Needs'),
    ('savings',         'Savings'),
    ('emergency_fund',  'Emergency Fund'),
    ('other',           'Other'),
]
VALID_EXPENSE_CATS = {c[0] for c in EXPENSE_CATEGORIES}
DEFAULT_SPLITS = {
    'daily':   {'food': Decimal('0.40'), 'transportation': Decimal('0.30'),
                'school_expenses': Decimal('0.10'), 'personal_needs': Decimal('0.05'),
                'savings': Decimal('0.10'), 'emergency_fund': Decimal('0.05'), 'other': Decimal('0.00')},
    'weekly':  {'food': Decimal('0.30'), 'transportation': Decimal('0.25'),
                'school_expenses': Decimal('0.15'), 'personal_needs': Decimal('0.05'),
                'savings': Decimal('0.15'), 'emergency_fund': Decimal('0.10'), 'other': Decimal('0.00')},
    'monthly': {'food': Decimal('0.30'), 'transportation': Decimal('0.20'),
                'school_expenses': Decimal('0.15'), 'personal_needs': Decimal('0.05'),
                'savings': Decimal('0.20'), 'emergency_fund': Decimal('0.10'), 'other': Decimal('0.00')},
}

# ── Utilities ──────────────────────────────────────────────────────────────

def _pk(v):
    v = str(v or '').strip()
    return int(v) if v.isdigit() else None

def _dec(v, default=None):
    try: return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError): return default

def _date(v):
    try: return datetime.datetime.strptime(v, '%Y-%m-%d').date()
    except (TypeError, ValueError): return None

def _month(v):
    for fmt in ('%Y-%m', '%Y-%m-%d'):
        try: return datetime.datetime.strptime(v, fmt).date().replace(day=1)
        except (TypeError, ValueError): continue
    return None

def _scoped(model, user, pk):
    pk = _pk(pk)
    return model.objects.filter(id=pk, user=user).first() if pk else None

# ── Auth lock helpers ──────────────────────────────────────────────────────

def _reset_lock(req):
    req.session['failed_attempts'] = 0
    req.session['lock_time'] = None

def _get_lock(req):
    fa = req.session.get('failed_attempts', 0)
    lt = req.session.get('lock_time')
    if not lt: return fa, None
    try: unlock = timezone.datetime.fromisoformat(lt)
    except (TypeError, ValueError): _reset_lock(req); return 0, None
    if timezone.now() < unlock:
        return fa, int((unlock - timezone.now()).total_seconds())
    _reset_lock(req); return 0, None

def _set_lock(req):
    until = timezone.now() + LOCK_DURATION
    req.session['failed_attempts'] = MAX_FAILED_ATTEMPTS
    req.session['lock_time'] = until.isoformat()
    return int(LOCK_DURATION.total_seconds())

def _validate_reg(data):
    errors = {}
    u = data.get('username','').strip()
    if not u: errors['username'] = 'Username is required.'
    elif User.objects.filter(username=u).exists(): errors['username'] = 'Username already taken.'
    if not data.get('first_name','').strip(): errors['first_name'] = 'First name is required.'
    if not data.get('last_name','').strip():  errors['last_name']  = 'Last name is required.'
    email = data.get('email','').strip()
    if not email: errors['email'] = 'Email is required.'
    else:
        try: validate_email(email)
        except ValidationError: errors['email'] = 'Enter a valid email address.'
        else:
            if User.objects.filter(email=email).exists(): errors['email'] = 'Email already registered.'
    pwd = data.get('password','')
    if not pwd: errors['password'] = 'Password is required.'
    elif len(pwd) < 8: errors['password'] = 'Password must be at least 8 characters.'
    else:
        try: validate_password(pwd)
        except ValidationError as e: errors['password'] = ' '.join(e.messages)
    if pwd != data.get('confirm_password',''): errors['confirm_password'] = 'Passwords do not match.'
    return errors

# ── Financial helpers (single source of truth) ─────────────────────────────

def _balance(user):
    """
    Available balance = Σ active allowances − Σ purchased items − Σ goal contributions.
    'Active' = is_deleted=False.
    """
    allowance_total = (
        Allowance.objects.filter(user=user, is_deleted=False)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )
    expense_total = (
        ExpenseItem.objects.filter(user=user, balance_deducted=True)
        .aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    )
    savings_total = (
        GoalContribution.objects.filter(user=user)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )
    return allowance_total - expense_total - savings_total

def _record_txn(user, txn_type, description, category, amount, date):
    """Append an immutable Transaction ledger row."""
    bal = _balance(user)
    Transaction.objects.create(
        user=user, transaction_type=txn_type, description=description,
        category=category, amount=amount, balance_after=bal, date=date,
    )

def _log(user, activity_type, description, amount, date,
         category='', status='completed', reason='',
         related_allowance_id=None, related_expense_id=None,
         original_description='', original_amount=None):
    """
    Append an immutable ActivityLog row.
    Must be called AFTER the financial change is committed to DB so that
    _balance() reflects the new state.
    balance_before is back-calculated from balance_after - amount.
    """
    bal_after = _balance(user)
    bal_before = (bal_after - amount) if amount is not None else bal_after
    ActivityLog.objects.create(
        user=user, activity_type=activity_type, description=description,
        category=category, amount=amount,
        balance_before=bal_before, balance_after=bal_after,
        status=status, reason=reason,
        related_allowance_id=related_allowance_id,
        related_expense_id=related_expense_id,
        original_description=original_description,
        original_amount=original_amount,
        date=date,
    )

def _period_dates(period, ref=None):
    today = ref or timezone.now().date()
    if period == 'daily':  return today, today
    if period == 'weekly':
        s = today - timedelta(today.weekday()); return s, s + timedelta(days=6)
    s = today.replace(day=1)
    if s.month == 12: e = s.replace(year=s.year+1, month=1, day=1) - timedelta(days=1)
    else:             e = s.replace(month=s.month+1, day=1) - timedelta(days=1)
    return s, e

def _spending_by_cat(user, start, end):
    items = ExpenseItem.objects.filter(user=user, balance_deducted=True, date__gte=start, date__lte=end)
    res = {k: Decimal('0') for k in BUDGET_CAT_LABELS}
    for item in items:
        bcat = EXPENSE_TO_BUDGET_CAT.get(item.category, 'other')
        res[bcat] = res.get(bcat, Decimal('0')) + item.total_cost
    return res

def _budget_tracking(budget, user):
    spending = _spending_by_cat(user, budget.period_start, budget.period_end)
    rows = []
    for cat, label in BUDGET_CAT_LABELS.items():
        planned = getattr(budget, cat, Decimal('0'))
        spent   = spending.get(cat, Decimal('0'))
        remain  = max(Decimal('0'), planned - spent)
        over    = spent > planned and planned > 0
        pct     = min(100, int(spent / planned * 100)) if planned > 0 else 0
        rows.append({'key': cat, 'label': label, 'planned': planned,
                     'spent': spent, 'remaining': remain, 'over_budget': over, 'pct': pct})
    return rows

def _suggest(amount, period):
    splits = DEFAULT_SPLITS.get(period, DEFAULT_SPLITS['weekly'])
    res = {}; assigned = Decimal('0'); keys = list(splits.keys())
    for i, cat in enumerate(keys):
        if i == len(keys) - 1: res[cat] = max(Decimal('0'), amount - assigned)
        else:
            v = (amount * splits[cat]).quantize(Decimal('1'))
            res[cat] = v; assigned += v
    return res

# ── Auth views ─────────────────────────────────────────────────────────────

@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated: return redirect('dashboard')
    fa, rem = _get_lock(request)
    if rem is not None:
        messages.error(request, f'Too many failed attempts. Try again in {rem} seconds.')
        return render(request, 'auth/login.html', {'locked': True, 'remaining': rem})
    if request.method == 'GET': return render(request, 'auth/login.html')
    username = request.POST.get('username','').strip()
    password = request.POST.get('password','')
    user = authenticate(request, username=username, password=password)
    if user:
        _reset_lock(request); login(request, user); return redirect('dashboard')
    fa += 1; request.session['failed_attempts'] = fa
    if fa >= MAX_FAILED_ATTEMPTS:
        rem = _set_lock(request)
        messages.error(request, 'Too many failed attempts. Locked for 1 minute.')
        return render(request, 'auth/login.html', {'locked': True, 'remaining': rem})
    messages.error(request, f'Invalid username or password. {MAX_FAILED_ATTEMPTS - fa} attempts remaining.')
    return render(request, 'auth/login.html')

def logout_view(request):
    logout(request); return redirect('login')

@require_http_methods(['GET', 'POST'])
def register_view(request):
    if request.user.is_authenticated: return redirect('dashboard')
    if request.method == 'GET': return render(request, 'auth/register.html')
    fd = {k: request.POST.get(k,'') for k in ['username','first_name','last_name','email','password','confirm_password']}
    errors = _validate_reg(fd)
    if errors: return render(request, 'auth/register.html', {'errors': errors, 'form_data': fd})
    user = User.objects.create_user(
        username=fd['username'].strip(), first_name=fd['first_name'].strip(),
        last_name=fd['last_name'].strip(), email=fd['email'].strip(), password=fd['password'],
    )
    login(request, user); return redirect('dashboard')

# ── Dashboard ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
def home(request):
    user = request.user
    today = timezone.now().date()
    vp = request.GET.get('period', 'weekly')
    if vp not in ('daily', 'weekly', 'monthly'): vp = 'weekly'
    ps, pe = _period_dates(vp, today)

    total_allowance = Allowance.objects.filter(user=user, is_deleted=False).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    total_expenses  = ExpenseItem.objects.filter(user=user, balance_deducted=True).aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    total_savings   = GoalContribution.objects.filter(user=user).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    remaining_balance = total_allowance - total_expenses - total_savings

    ds, de = _period_dates('daily', today)
    ws, we = _period_dates('weekly', today)
    ms, me = _period_dates('monthly', today)

    daily_spending   = ExpenseItem.objects.filter(user=user, balance_deducted=True, date__gte=ds, date__lte=de).aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    weekly_spending  = ExpenseItem.objects.filter(user=user, balance_deducted=True, date__gte=ws, date__lte=we).aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    monthly_spending = ExpenseItem.objects.filter(user=user, balance_deducted=True, date__gte=ms, date__lte=me).aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    period_spending  = ExpenseItem.objects.filter(user=user, balance_deducted=True, date__gte=ps, date__lte=pe).aggregate(t=Sum('total_cost'))['t'] or Decimal('0')

    today_allowance  = Allowance.objects.filter(user=user, is_deleted=False, date_received=today).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    today_savings    = GoalContribution.objects.filter(user=user, date=today).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    cat_spending = _spending_by_cat(user, ps, pe)
    active_budget = AllowanceBudget.objects.filter(user=user, period=vp, period_start__lte=today, period_end__gte=today).order_by('-created_at').first()
    b_tracking = _budget_tracking(active_budget, user) if active_budget else []

    warnings = []
    if active_budget:
        for row in b_tracking:
            if row['planned'] > 0 and row['pct'] >= 80:
                lbl = 'exceeded' if row['over_budget'] else 'almost at limit'
                warnings.append(f'{row["label"]} budget is {lbl} (₱{row["spent"]:.2f} / ₱{row["planned"]:.2f})')

    savings_goals = SavingsGoal.objects.filter(user=user)
    recent_activity = ActivityLog.objects.filter(user=user).order_by('-timestamp')[:12]

    ai_insights = _build_ai_insights(user, vp, today)

    return render(request, 'home.html', {
        'total_allowance': total_allowance, 'total_expenses': total_expenses,
        'total_savings': total_savings, 'remaining_balance': remaining_balance,
        'today_allowance': today_allowance, 'today_spending': daily_spending,
        'today_savings': today_savings,
        'daily_spending': daily_spending, 'weekly_spending': weekly_spending,
        'monthly_spending': monthly_spending, 'period_spending': period_spending,
        'period_start': ps, 'period_end': pe, 'view_period': vp,
        'cat_spending': cat_spending, 'cat_labels': BUDGET_CAT_LABELS,
        'active_budget': active_budget, 'budget_tracking': b_tracking,
        'warnings': warnings, 'savings_goals': savings_goals,
        'recent_activity': recent_activity, 'today': today,
        'weekly_start': ws, 'weekly_end': we,
        'monthly_start': ms, 'monthly_end': me,
        'ai': ai_insights,
    })

# ── My Allowance ───────────────────────────────────────────────────────────

@login_required(login_url='login')
def my_allowance(request):
    user  = request.user
    today = timezone.now().date()
    allowances_active  = user.allowances.filter(is_deleted=False).order_by('-date_received', '-created_at')
    allowances_deleted = user.allowances.filter(is_deleted=True).order_by('-deleted_at')
    budget_plans       = AllowanceBudget.objects.filter(user=user).order_by('-period_start')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_allowance':
            amount = _dec(request.POST.get('amount'))
            if amount is None or amount <= 0:
                messages.error(request, 'Amount must be greater than 0.'); return redirect('my_allowance')
            date = _date(request.POST.get('date_received'))
            if date is None:
                messages.error(request, 'A valid date is required.'); return redirect('my_allowance')
            atype = request.POST.get('allowance_type', 'weekly')
            if atype not in [c[0] for c in Allowance.ALLOWANCE_TYPES]: atype = 'weekly'
            with db_transaction.atomic():
                a = Allowance.objects.create(
                    name=request.POST.get('name','Allowance') or 'Allowance',
                    amount=amount, allowance_type=atype,
                    date_received=date, description=request.POST.get('description',''), user=user,
                )
                _record_txn(user, 'allowance_added', a.name, 'Allowance', amount, date)
                _log(user, 'allowance_added', f'Allowance added: {a.name}',
                     amount, date, category=a.get_allowance_type_display(),
                     related_allowance_id=a.id)
            messages.success(request, f'Allowance of ₱{amount:.2f} added.')
            return redirect('my_allowance')

        elif action == 'save_budget':
            period = request.POST.get('budget_period', 'weekly')
            if period not in ('daily','weekly','monthly'): period = 'weekly'
            total_amount = _dec(request.POST.get('budget_total_amount'))
            if total_amount is None or total_amount <= 0:
                messages.error(request, 'Budget amount must be greater than 0.'); return redirect('my_allowance')
            period_start = _date(request.POST.get('budget_period_start'))
            if period_start is None:
                messages.error(request, 'Budget start date is required.'); return redirect('my_allowance')
            _, period_end = _period_dates(period, period_start)
            cats = {}; total_alloc = Decimal('0')
            for cat in AllowanceBudget.BUDGET_CATEGORIES:
                v = _dec(request.POST.get(f'alloc_{cat}'), Decimal('0'))
                cats[cat] = max(Decimal('0'), v); total_alloc += cats[cat]
            if total_alloc > total_amount:
                messages.error(request, f'Total allocation (₱{total_alloc:.2f}) exceeds budget (₱{total_amount:.2f}).')
                return redirect('my_allowance')
            aid = _pk(request.POST.get('budget_allowance_id'))
            linked = Allowance.objects.filter(id=aid, user=user, is_deleted=False).first() if aid else None
            bpk = _pk(request.POST.get('budget_id'))
            plan_name = request.POST.get('budget_plan_name', '').strip()[:120]
            with db_transaction.atomic():
                bp = AllowanceBudget.objects.filter(id=bpk, user=user).first() if bpk else \
                     AllowanceBudget.objects.filter(user=user, period=period, period_start=period_start).first()
                if bp:
                    bp.total_amount = total_amount; bp.period_end = period_end
                    bp.allowance = linked; bp.notes = request.POST.get('budget_notes','')
                    bp.name = plan_name
                    for cat in AllowanceBudget.BUDGET_CATEGORIES: setattr(bp, cat, cats[cat])
                    bp.save()
                    action_word = 'updated'
                else:
                    bp = AllowanceBudget.objects.create(
                        user=user, period=period, period_start=period_start, period_end=period_end,
                        total_amount=total_amount, allowance=linked, name=plan_name,
                        notes=request.POST.get('budget_notes',''), **cats)
                    action_word = 'saved'
                _log(user, 'budget_saved', f'Budget plan {action_word}: {bp.display_name()} ₱{total_amount:.2f}',
                     None, today, category='Budget Plan')
            messages.success(request, f'Budget plan {action_word}: {bp.display_name()} — No money was deducted.')
            return redirect('my_allowance')

        elif action == 'archive_budget':
            bpk = _pk(request.POST.get('budget_id'))
            bp = AllowanceBudget.objects.filter(id=bpk, user=user).first()
            if bp:
                bp.is_archived = not bp.is_archived
                bp.save(update_fields=['is_archived', 'updated_at'])
                verb = 'archived' if bp.is_archived else 'unarchived'
                messages.success(request, f'Plan {verb}: {bp.display_name()}')
            else:
                messages.error(request, 'Plan not found.')
            return redirect('my_allowance')

        elif action == 'rename_budget':
            bpk = _pk(request.POST.get('budget_id'))
            new_name = request.POST.get('budget_plan_name', '').strip()[:120]
            bp = AllowanceBudget.objects.filter(id=bpk, user=user).first()
            if bp and new_name:
                bp.name = new_name
                bp.save(update_fields=['name', 'updated_at'])
                messages.success(request, f'Plan renamed to: {new_name}')
            elif not new_name:
                messages.error(request, 'Plan name cannot be empty.')
            else:
                messages.error(request, 'Plan not found.')
            return redirect('my_allowance')

        elif action == 'delete_budget':
            bpk = _pk(request.POST.get('budget_id'))
            bp = AllowanceBudget.objects.filter(id=bpk, user=user).first()
            if bp: bp.delete(); messages.success(request, 'Budget plan deleted.')
            return redirect('my_allowance')

    ws, we = _period_dates('weekly', today)
    weekly_allowance = allowances_active.filter(date_received__gte=ws, date_received__lte=we).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    balance = _balance(user)
    today_allowance = allowances_active.filter(date_received=today).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    active_plan = AllowanceBudget.objects.filter(user=user, is_archived=False, period_start__lte=today, period_end__gte=today).order_by('-created_at').first()
    bt = _budget_tracking(active_plan, user) if active_plan else []
    active_plan_total_spent = sum(r['spent'] for r in bt) if bt else Decimal('0')
    daily_equiv = active_plan.daily_amount() if active_plan and active_plan.period != 'daily' else None

    # Build plans_with_tracking — active (non-archived) plans only, avoid double call
    budget_plans = AllowanceBudget.objects.filter(user=user, is_archived=False).order_by('-period_start')
    plans_with_tracking = []
    for bp in budget_plans:
        tracking = _budget_tracking(bp, user)
        plans_with_tracking.append({
            'plan': bp,
            'tracking': tracking,
            'total_spent': sum(r['spent'] for r in tracking),
        })

    # Archived plans shown separately
    archived_plans = AllowanceBudget.objects.filter(user=user, is_archived=True).order_by('-period_start')

    return render(request, 'my_allowance.html', {
        'allowances': allowances_active, 'allowances_deleted': allowances_deleted,
        'weekly_allowance': weekly_allowance, 'today': today,
        'weekly_start': ws, 'weekly_end': we,
        'balance': balance, 'today_allowance': today_allowance,
        'active_plan': active_plan, 'budget_tracking': bt,
        'daily_equiv': daily_equiv, 'active_plan_total_spent': active_plan_total_spent,
        'budget_plans': budget_plans, 'plans_with_tracking': plans_with_tracking,
        'archived_plans': archived_plans,
        'budget_cat_labels': BUDGET_CAT_LABELS, 'budget_categories': AllowanceBudget.BUDGET_CATEGORIES,
    })

# ── Allowance delete confirmation ──────────────────────────────────────────

@login_required(login_url='login')
def allowance_delete_confirm(request, pk):
    allowance = Allowance.objects.filter(id=pk, user=request.user, is_deleted=False).first()
    if allowance is None:
        messages.error(request, 'Allowance not found or already deleted.')
        return redirect('my_allowance')
    if request.method == 'GET':
        bal_now   = _balance(request.user)
        bal_after = bal_now - allowance.amount
        return render(request, 'allowance_delete_confirm.html', {
            'allowance': allowance, 'balance_now': bal_now, 'balance_after_deletion': bal_after,
        })
    reason = request.POST.get('reason','').strip()
    with db_transaction.atomic():
        allowance.soft_delete(reason=reason)
        # Record in Transaction ledger so List page shows it
        _record_txn(
            user=request.user, txn_type='allowance_deleted',
            description=f'Allowance Record Deleted: {allowance.name} (₱{allowance.amount:.2f})',
            category='Allowance', amount=-allowance.amount, date=timezone.now().date(),
        )
        # Immutable audit entry
        _log(request.user, 'allowance_deleted',
             f'Allowance record deleted: {allowance.name}',
             amount=-allowance.amount, date=timezone.now().date(),
             category=allowance.get_allowance_type_display(),
             status='deleted', reason=reason or 'User deleted allowance record',
             related_allowance_id=allowance.id,
             original_description=allowance.name, original_amount=allowance.amount)
    messages.success(
        request,
        f'Allowance "{allowance.name}" (₱{allowance.amount:.2f}) deleted and recorded in activity history.'
    )
    return redirect('my_allowance')

# ── Allowance AJAX (modal edit — deletion goes via confirm page) ───────────

@login_required(login_url='login')
@require_POST
def allowance_save_ajax(request):
    pk = request.POST.get('id'); is_upd = bool(pk)
    if is_upd:
        a = _scoped(Allowance, request.user, pk)
        if a is None or a.is_deleted: return JsonResponse({'error': 'Allowance not found.'}, status=404)
    else:
        a = Allowance()
    amount = _dec(request.POST.get('amount'))
    if amount is None or amount <= 0: return JsonResponse({'error': 'Amount must be greater than 0.'}, status=400)
    date = _date(request.POST.get('date_received'))
    if date is None: return JsonResponse({'error': 'A valid date is required.'}, status=400)
    a.name = request.POST.get('name','Allowance') or 'Allowance'
    a.amount = amount; a.allowance_type = request.POST.get('allowance_type','weekly')
    a.date_received = date; a.description = request.POST.get('description','').strip()
    if not a.pk: a.user = request.user
    with db_transaction.atomic():
        a.save()
        if not is_upd:
            _record_txn(request.user, 'allowance_added', a.name, 'Allowance', amount, date)
            _log(request.user, 'allowance_added', f'Allowance added: {a.name}',
                 amount, date, category=a.get_allowance_type_display(), related_allowance_id=a.id)
    messages.success(request, f'Allowance {"updated" if is_upd else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': a.id})

@login_required(login_url='login')
@require_POST
def allowance_delete_ajax(request, pk):
    # All deletions must go through the confirmation page for audit safety
    return JsonResponse({
        'status': 'confirm_required',
        'redirect': f'/allowance/delete-confirm/{pk}/',
        'message': 'Deletion requires confirmation to be recorded in activity history.',
    })

# ── Budget suggestion AJAX ─────────────────────────────────────────────────

@login_required(login_url='login')
@require_POST
def budget_suggest_ajax(request):
    amount = _dec(request.POST.get('amount'))
    period = request.POST.get('period', 'weekly')
    if amount is None or amount <= 0: return JsonResponse({'error': 'Enter an amount greater than 0.'}, status=400)
    if period not in DEFAULT_SPLITS: period = 'weekly'
    sugg = _suggest(amount, period)
    result = {cat: float(v) for cat, v in sugg.items()}
    result.update({'total': float(amount), 'period': period, 'labels': BUDGET_CAT_LABELS})
    return JsonResponse(result)

# ── Expense Items ──────────────────────────────────────────────────────────

@login_required(login_url='login')
def expense_items_page(request):
    user = request.user
    expense_items = ExpenseItem.objects.filter(user=user).order_by('-date', '-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        # ── Add new expense item ───────────────────────────────────────────
        if action == 'add_expense_item':
            name = request.POST.get('name','').strip()
            if not name:
                messages.error(request, 'Item name is required.')
                return redirect('expense_items')
            # Normalise category — map legacy school_supplies → school_expenses
            raw_cat = request.POST.get('category', 'other')
            cat = EXPENSE_TO_BUDGET_CAT.get(raw_cat, 'other') if raw_cat not in VALID_EXPENSE_CATS else raw_cat
            qty   = _dec(request.POST.get('quantity', '1'))
            price = _dec(request.POST.get('price_per_item'))
            date  = _date(request.POST.get('date'))
            if qty is None or qty < 1:
                messages.error(request, 'Quantity must be at least 1.')
                return redirect('expense_items')
            if price is None or price <= 0:
                messages.error(request, 'Price must be greater than 0.')
                return redirect('expense_items')
            if date is None:
                messages.error(request, 'A valid date is required.')
                return redirect('expense_items')
            # Accept requested status; default to planned
            req_status = request.POST.get('status', 'planned')
            if req_status not in ('planned', 'purchased', 'cancelled'):
                req_status = 'planned'
            qty = int(qty)
            total = Decimal(str(qty)) * price

            # If immediately purchased, check balance first
            if req_status == 'purchased':
                bal = _balance(user)
                if total > bal:
                    messages.error(request, f'Insufficient balance. You have ₱{bal:.2f} but need ₱{total:.2f}.')
                    return redirect('expense_items')

            with db_transaction.atomic():
                item = ExpenseItem.objects.create(
                    name=name, category=cat, quantity=qty,
                    price_per_item=price, total_cost=total,
                    status=req_status,
                    balance_deducted=(req_status == 'purchased'),
                    date=date, user=user,
                )
                if req_status == 'purchased':
                    _record_txn(user, 'purchase', item.name, item.get_category_display(),
                                -item.total_cost, item.date)
                    _log(user, 'expense_purchased', f'Purchase: {item.name}',
                         -item.total_cost, item.date, category=item.get_category_display(),
                         related_expense_id=item.id, original_description=name, original_amount=total)
                elif req_status == 'cancelled':
                    _log(user, 'expense_cancelled', f'Expense cancelled on add: {item.name}',
                         None, date, category=item.get_category_display(),
                         related_expense_id=item.id, status='reversed',
                         original_description=name, original_amount=total)
                else:
                    _log(user, 'expense_planned', f'Expense planned: {name}',
                         None, date, category=item.get_category_display(),
                         related_expense_id=item.id, original_description=name, original_amount=total)
            messages.success(request, f'{name} added ({item.get_status_display()}).')
            return redirect('expense_items')

        # ── Edit existing expense item ─────────────────────────────────────
        elif action == 'edit_expense_item':
            ipk = _pk(request.POST.get('item_id'))
            item = ExpenseItem.objects.filter(id=ipk, user=user).first() if ipk else None
            if item is None:
                messages.error(request, 'Expense item not found.')
                return redirect('expense_items')

            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Item name is required.')
                return redirect('expense_items')
            raw_cat = request.POST.get('category', 'other')
            cat = EXPENSE_TO_BUDGET_CAT.get(raw_cat, 'other') if raw_cat not in VALID_EXPENSE_CATS else raw_cat
            qty   = _dec(request.POST.get('quantity', '1'))
            price = _dec(request.POST.get('price_per_item'))
            date  = _date(request.POST.get('date'))
            if qty is None or qty < 1:
                messages.error(request, 'Quantity must be at least 1.')
                return redirect('expense_items')
            if price is None or price <= 0:
                messages.error(request, 'Price must be greater than 0.')
                return redirect('expense_items')
            if date is None:
                messages.error(request, 'A valid date is required.')
                return redirect('expense_items')

            new_status = request.POST.get('status', item.status)
            if new_status not in ('planned', 'purchased', 'cancelled'):
                new_status = item.status

            qty = int(qty)
            new_total = Decimal(str(qty)) * price
            old_status = item.status
            old_deducted = item.balance_deducted
            old_total = item.total_cost
            today = timezone.now().date()

            with db_transaction.atomic():
                # Determine if financial change is needed
                # Case A: was purchased (deducted) → now cancelled/planned → refund old amount
                if old_deducted and new_status in ('cancelled', 'planned'):
                    item.status = new_status
                    item.balance_deducted = False
                    item.name = name; item.category = cat
                    item.quantity = qty; item.price_per_item = price; item.total_cost = new_total
                    item.date = date
                    item.save()
                    _record_txn(user, 'refund', f'Refund (edit): {name}',
                                item.get_category_display(), old_total, today)
                    _log(user, 'refund', f'Refund on edit/cancel: {name}',
                         old_total, today, category=item.get_category_display(),
                         related_expense_id=item.id, original_description=name,
                         original_amount=old_total)

                # Case B: was NOT deducted → now purchased → deduct new amount
                elif not old_deducted and new_status == 'purchased':
                    bal = _balance(user)
                    if new_total > bal:
                        messages.error(request, f'Insufficient balance. You have ₱{bal:.2f} but need ₱{new_total:.2f}.')
                        return redirect('expense_items')
                    item.status = 'purchased'
                    item.balance_deducted = True
                    item.name = name; item.category = cat
                    item.quantity = qty; item.price_per_item = price; item.total_cost = new_total
                    item.date = date
                    item.save()
                    _record_txn(user, 'purchase', f'{name}',
                                item.get_category_display(), -new_total, date)
                    _log(user, 'expense_purchased', f'Purchase (via edit): {name}',
                         -new_total, date, category=item.get_category_display(),
                         related_expense_id=item.id, original_description=name,
                         original_amount=new_total)

                # Case C: was purchased → still purchased, amount changed → adjust
                elif old_deducted and new_status == 'purchased':
                    diff = new_total - old_total   # positive = more spent, negative = less
                    if diff != Decimal('0'):
                        bal = _balance(user)
                        if diff > 0 and diff > bal:
                            messages.error(request, f'Insufficient balance for the increased amount (₱{diff:.2f} more needed).')
                            return redirect('expense_items')
                        item.name = name; item.category = cat
                        item.quantity = qty; item.price_per_item = price; item.total_cost = new_total
                        item.date = date
                        item.save()
                        # Record correction
                        _record_txn(user, 'correction', f'Edit correction: {name}',
                                    item.get_category_display(), -diff, today)
                        _log(user, 'correction', f'Expense edited: {name} (amount adjusted by ₱{diff:.2f})',
                             -diff, today, category=item.get_category_display(),
                             related_expense_id=item.id)
                    else:
                        # Same amount, just update meta fields
                        item.name = name; item.category = cat
                        item.quantity = qty; item.price_per_item = price
                        item.date = date
                        item.save()

                # Case D: no financial change — update metadata only
                else:
                    item.name = name; item.category = cat
                    item.quantity = qty; item.price_per_item = price; item.total_cost = new_total
                    item.date = date; item.status = new_status
                    item.save()

            messages.success(request, f'{name} updated.')
            return redirect('expense_items')

        elif action == 'mark_purchased':
            ipk = _pk(request.POST.get('item_id'))
            item = ExpenseItem.objects.filter(id=ipk, user=user).first() if ipk else None
            if item is None: messages.error(request, 'Item not found.'); return redirect('expense_items')
            if item.balance_deducted: messages.warning(request, 'Already marked as purchased.'); return redirect('expense_items')
            bal = _balance(user)
            if item.total_cost > bal:
                messages.error(request, f'Insufficient balance. You have ₱{bal:.2f} but need ₱{item.total_cost:.2f}.')
                return redirect('expense_items')
            with db_transaction.atomic():
                item.status = 'purchased'; item.balance_deducted = True; item.save()
                _record_txn(user, 'purchase', item.name, item.get_category_display(), -item.total_cost, item.date)
                _log(user, 'expense_purchased', f'Purchase: {item.name}',
                     -item.total_cost, item.date, category=item.get_category_display(),
                     related_expense_id=item.id)
            messages.success(request, f'{item.name} purchased. ₱{item.total_cost:.2f} deducted.')
            return redirect('expense_items')

        elif action == 'cancel_item':
            ipk = _pk(request.POST.get('item_id'))
            item = ExpenseItem.objects.filter(id=ipk, user=user).first() if ipk else None
            if item is None: messages.error(request, 'Item not found.'); return redirect('expense_items')
            if item.status == 'purchased' and item.balance_deducted:
                with db_transaction.atomic():
                    item.status = 'cancelled'; item.balance_deducted = False; item.save()
                    _record_txn(user, 'refund', f'Refund: {item.name}', item.get_category_display(), item.total_cost, timezone.now().date())
                    _log(user, 'refund', f'Refund: {item.name}', item.total_cost, timezone.now().date(),
                         category=item.get_category_display(), related_expense_id=item.id)
                messages.success(request, f'Refund processed. ₱{item.total_cost:.2f} returned.')
            else:
                with db_transaction.atomic():
                    item.status = 'cancelled'; item.save()
                    _log(user, 'expense_cancelled', f'Expense cancelled: {item.name}',
                         None, timezone.now().date(), category=item.get_category_display(),
                         related_expense_id=item.id, status='reversed')
                messages.success(request, f'{item.name} cancelled.')
            return redirect('expense_items')

        elif action == 'delete_item':
            ipk = _pk(request.POST.get('item_id'))
            item = ExpenseItem.objects.filter(id=ipk, user=user).first() if ipk else None
            if item is None: messages.error(request, 'Item not found.'); return redirect('expense_items')
            if item.balance_deducted: messages.error(request, 'Cannot delete a purchased item. Refund it first.'); return redirect('expense_items')
            with db_transaction.atomic():
                _log(user, 'expense_deleted', f'Expense record deleted: {item.name}',
                     None, timezone.now().date(), category=item.get_category_display(),
                     status='deleted', original_description=item.name, original_amount=item.total_cost)
                item.delete()
            messages.success(request, 'Expense item deleted.')
            return redirect('expense_items')

    return render(request, 'expense_items.html', {
        'expense_items': expense_items,
        'balance': _balance(user),
        'today': timezone.now().date(),
        'expense_categories': EXPENSE_CATEGORIES,
    })

# ── Goals ──────────────────────────────────────────────────────────────────

@login_required(login_url='login')
def goals_page(request):
    user = request.user
    savings_goals = SavingsGoal.objects.filter(user=user).order_by('-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_goal':
            name = request.POST.get('name','').strip()
            if not name: messages.error(request, 'Goal name is required.'); return redirect('goals')
            target = _dec(request.POST.get('target_amount'))
            if target is None or target <= 0: messages.error(request, 'Target amount must be > 0.'); return redirect('goals')
            dl = _date(request.POST.get('deadline','')) if request.POST.get('deadline','').strip() else None
            SavingsGoal.objects.create(name=name, target_amount=target, current_amount=Decimal('0'),
                                       deadline=dl, status='in_progress', user=user)
            messages.success(request, f'Goal "{name}" created.')
            return redirect('goals')

        elif action == 'add_contribution':
            gid = _pk(request.POST.get('goal_id'))
            goal = SavingsGoal.objects.filter(id=gid, user=user).first() if gid else None
            if goal is None: messages.error(request, 'Goal not found.'); return redirect('goals')
            amount = _dec(request.POST.get('contribution_amount'))
            if amount is None or amount <= 0: messages.error(request, 'Amount must be > 0.'); return redirect('goals')
            bal = _balance(user)
            if amount > bal: messages.error(request, f'Insufficient balance. Available: ₱{bal:.2f}.'); return redirect('goals')
            note = request.POST.get('note','').strip()
            today = timezone.now().date()
            with db_transaction.atomic():
                GoalContribution.objects.create(goal=goal, amount=amount, note=note, date=today, user=user)
                goal.current_amount = Decimal(str(goal.current_amount)) + amount; goal.save()
                goal.check_and_mark_completed()
                _record_txn(user, 'savings_transfer', goal.name, 'Savings Goal', -amount, today)
                _log(user, 'savings_transfer', f'Savings transfer to: {goal.name}',
                     -amount, today, category='Savings Goal')
            messages.success(request, f'₱{amount:.2f} added to "{goal.name}".')
            return redirect('goals')

        elif action == 'delete_goal':
            gid = _pk(request.POST.get('goal_id'))
            goal = SavingsGoal.objects.filter(id=gid, user=user).first() if gid else None
            if goal is None: messages.error(request, 'Goal not found.'); return redirect('goals')
            contribs = GoalContribution.objects.filter(goal=goal, user=user)
            total_refund = contribs.aggregate(t=Sum('amount'))['t'] or Decimal('0')
            today = timezone.now().date()
            if total_refund > 0:
                with db_transaction.atomic():
                    contribs.delete(); goal.delete()
                    _record_txn(user, 'refund', f'Goal deleted: {goal.name}', 'Savings Goal', total_refund, today)
                    _log(user, 'goal_deleted', f'Goal deleted: {goal.name}', total_refund, today,
                         category='Savings Goal', status='deleted',
                         original_description=goal.name, original_amount=goal.target_amount)
                messages.success(request, f'Goal deleted. ₱{total_refund:.2f} returned to balance.')
            else:
                with db_transaction.atomic():
                    goal.delete()
                    _log(user, 'goal_deleted', f'Goal deleted: {goal.name}', None, today,
                         category='Savings Goal', status='deleted')
                messages.success(request, 'Goal deleted.')
            return redirect('goals')

    bal = _balance(user)
    goals_with_data = [{'goal': g, 'contributions': GoalContribution.objects.filter(goal=g, user=user).order_by('-date','-created_at')} for g in savings_goals]
    return render(request, 'goals.html', {
        'savings_goals': savings_goals, 'goals_with_data': goals_with_data,
        'balance': bal, 'today': timezone.now().date(),
    })

# ── Transaction / Activity List ────────────────────────────────────────────

@login_required(login_url='login')
def transaction_list_page(request):
    user = request.user
    # Use ActivityLog as the primary source — it has full detail
    logs = ActivityLog.objects.filter(user=user).order_by('-timestamp')

    # Filters
    type_filter  = request.GET.get('type','').strip()
    date_from    = request.GET.get('date_from','').strip()
    date_to      = request.GET.get('date_to','').strip()
    cat_filter   = request.GET.get('category','').strip()

    if type_filter: logs = logs.filter(activity_type=type_filter)
    if date_from:
        d = _date(date_from)
        if d: logs = logs.filter(date__gte=d)
    if date_to:
        d = _date(date_to)
        if d: logs = logs.filter(date__lte=d)
    if cat_filter: logs = logs.filter(category__icontains=cat_filter)

    return render(request, 'transaction_list.html', {
        'activity_logs': logs, 'balance': _balance(user),
        'type_filter': type_filter, 'date_from': date_from,
        'date_to': date_to, 'cat_filter': cat_filter,
        'activity_types': ActivityLog.ACTIVITY_TYPES,
    })

# ── Profile ────────────────────────────────────────────────────────────────

@login_required(login_url='login')
def profile_page(request):
    user = request.user
    if request.method == 'POST':
        fn = request.POST.get('first_name','').strip()
        ln = request.POST.get('last_name','').strip()
        em = request.POST.get('email','').strip()
        if fn: user.first_name = fn
        if ln: user.last_name  = ln
        if em: user.email      = em
        user.save()
        messages.success(request, 'Profile updated.')
        return redirect('profile')
    return render(request, 'profile.html', {'user': user})

# ── Legacy redirects ───────────────────────────────────────────────────────

@login_required(login_url='login')
def expenses_page(request): return redirect('expense_items')

@login_required(login_url='login')
def savings_goals_page(request): return redirect('goals')

@login_required(login_url='login')
def reports_page(request): return redirect('transaction_list')

@login_required(login_url='login')
def budget_planner_page(request): return redirect('my_allowance')

@login_required(login_url='login')
def food_list_page(request): return redirect('dashboard')

@login_required(login_url='login')
def suggestions_page(request): return redirect('my_allowance')

# ── Legacy AJAX (preserved so existing JS still works) ────────────────────

@login_required(login_url='login')
@require_POST
def income_save_ajax(request):
    pk = request.POST.get('id')
    income = _scoped(Income, request.user, pk) if pk else Income()
    if pk and income is None: return JsonResponse({'error': 'Not found.'}, status=404)
    amount = _dec(request.POST.get('amount'))
    date   = _date(request.POST.get('date'))
    if amount is None or amount <= 0: return JsonResponse({'error': 'Amount must be > 0.'}, status=400)
    if date is None: return JsonResponse({'error': 'Valid date required.'}, status=400)
    income.amount = amount; income.source = request.POST.get('source','allowance')
    income.description = request.POST.get('description','').strip(); income.date = date
    if not income.pk: income.user = request.user
    income.save()
    return JsonResponse({'status': 'success', 'id': income.id})

@login_required(login_url='login')
@require_POST
def income_delete_ajax(request, pk):
    income = _scoped(Income, request.user, pk)
    if income is None: return JsonResponse({'error': 'Not found.'}, status=404)
    income.delete()
    return JsonResponse({'status': 'deleted'})

@login_required(login_url='login')
@require_POST
def expense_save_ajax(request):
    pk = request.POST.get('id')
    exp = _scoped(Expense, request.user, pk) if pk else Expense()
    if pk and exp is None: return JsonResponse({'error': 'Not found.'}, status=404)
    amount = _dec(request.POST.get('amount'))
    date   = _date(request.POST.get('date'))
    if amount is None or amount <= 0: return JsonResponse({'error': 'Amount must be > 0.'}, status=400)
    if date is None: return JsonResponse({'error': 'Valid date required.'}, status=400)
    cat = Category.objects.filter(pk=_pk(request.POST.get('category')), user=request.user).first()
    exp.amount = amount; exp.description = request.POST.get('description','').strip()
    exp.date = date; exp.category = cat
    if not exp.pk: exp.user = request.user
    exp.save()
    return JsonResponse({'status': 'success', 'id': exp.id})

@login_required(login_url='login')
@require_POST
def expense_delete_ajax(request, pk):
    exp = _scoped(Expense, request.user, pk)
    if exp is None: return JsonResponse({'error': 'Not found.'}, status=404)
    exp.delete(); return JsonResponse({'status': 'deleted'})

@login_required(login_url='login')
@require_POST
def category_save_ajax(request):
    pk = request.POST.get('id')
    cat = _scoped(Category, request.user, pk) if pk else Category()
    if pk and cat is None: return JsonResponse({'error': 'Not found.'}, status=404)
    name = request.POST.get('name','').strip()
    if not name: return JsonResponse({'error': 'Name required.'}, status=400)
    cat.name = name; cat.category_type = request.POST.get('category_type','need')
    if not cat.pk: cat.user = request.user
    try: cat.save()
    except IntegrityError: return JsonResponse({'error': 'Category name already exists.'}, status=400)
    return JsonResponse({'status': 'success', 'id': cat.id})

@login_required(login_url='login')
@require_POST
def category_delete_ajax(request, pk):
    cat = _scoped(Category, request.user, pk)
    if cat is None: return JsonResponse({'error': 'Not found.'}, status=404)
    cat.delete(); return JsonResponse({'status': 'deleted'})

@login_required(login_url='login')
@require_POST
def budget_save_ajax(request):
    pk = request.POST.get('id')
    plan = _scoped(BudgetPlan, request.user, pk) if pk else BudgetPlan()
    if pk and plan is None: return JsonResponse({'error': 'Not found.'}, status=404)
    cat = Category.objects.filter(pk=_pk(request.POST.get('category')), user=request.user).first()
    if cat is None: return JsonResponse({'error': 'Valid category required.'}, status=400)
    mi = _dec(request.POST.get('monthly_income'))
    aa = _dec(request.POST.get('allocated_amount'))
    mo = _month(request.POST.get('month'))
    if mi is None or mi < 0: return JsonResponse({'error': 'Monthly income must be >= 0.'}, status=400)
    if aa is None or aa < 0: return JsonResponse({'error': 'Allocated amount must be >= 0.'}, status=400)
    if mo is None: return JsonResponse({'error': 'Valid month required.'}, status=400)
    plan.category = cat; plan.monthly_income = mi; plan.allocated_amount = aa; plan.month = mo
    if not plan.pk: plan.user = request.user
    try: plan.save()
    except IntegrityError: return JsonResponse({'error': 'Plan already exists for that category+month.'}, status=400)
    return JsonResponse({'status': 'success', 'id': plan.id})

@login_required(login_url='login')
@require_POST
def budget_delete_ajax(request, pk):
    plan = _scoped(BudgetPlan, request.user, pk)
    if plan is None: return JsonResponse({'error': 'Not found.'}, status=404)
    plan.delete(); return JsonResponse({'status': 'deleted'})

@login_required(login_url='login')
@require_POST
def savings_save_ajax(request):
    pk = request.POST.get('id')
    goal = _scoped(SavingsGoal, request.user, pk) if pk else SavingsGoal()
    if pk and goal is None: return JsonResponse({'error': 'Not found.'}, status=404)
    name = request.POST.get('name','').strip()
    ta   = _dec(request.POST.get('target_amount'))
    ca   = _dec(request.POST.get('current_amount'), Decimal('0'))
    if not name: return JsonResponse({'error': 'Name required.'}, status=400)
    if ta is None or ta <= 0: return JsonResponse({'error': 'Target > 0 required.'}, status=400)
    if ca is None or ca < 0:  return JsonResponse({'error': 'Current amount >= 0.'}, status=400)
    dl = _date(request.POST.get('deadline','')) if request.POST.get('deadline','').strip() else None
    goal.name = name; goal.target_amount = ta; goal.current_amount = ca; goal.deadline = dl
    if not goal.pk: goal.user = request.user
    goal.save()
    return JsonResponse({'status': 'success', 'id': goal.id})

@login_required(login_url='login')
@require_POST
def savings_delete_ajax(request, pk):
    goal = _scoped(SavingsGoal, request.user, pk)
    if goal is None: return JsonResponse({'error': 'Not found.'}, status=404)
    goal.delete(); return JsonResponse({'status': 'deleted'})

@login_required(login_url='login')
@require_POST
def grocery_save_ajax(request):
    pk = request.POST.get('id')
    item = _scoped(GroceryItem, request.user, pk) if pk else GroceryItem()
    if pk and item is None: return JsonResponse({'error': 'Not found.'}, status=404)
    name = request.POST.get('name','').strip()
    if not name: return JsonResponse({'error': 'Name required.'}, status=400)
    try: qty = int(request.POST.get('quantity','1'))
    except (TypeError, ValueError): return JsonResponse({'error': 'Quantity must be integer.'}, status=400)
    if qty < 1: return JsonResponse({'error': 'Quantity >= 1.'}, status=400)
    ep = _dec(request.POST.get('estimated_price'), Decimal('0'))
    if ep is None or ep < 0: return JsonResponse({'error': 'Price >= 0.'}, status=400)
    item.name = name; item.quantity = qty; item.estimated_price = ep
    if not item.pk: item.user = request.user
    item.save()
    return JsonResponse({'status': 'success', 'id': item.id})

@login_required(login_url='login')
@require_POST
def grocery_toggle_ajax(request, pk):
    item = _scoped(GroceryItem, request.user, pk)
    if item is None: return JsonResponse({'error': 'Not found.'}, status=404)
    item.is_purchased = not item.is_purchased; item.save()
    return JsonResponse({'status': 'success', 'is_purchased': item.is_purchased})

@login_required(login_url='login')
@require_POST
def grocery_delete_ajax(request, pk):
    item = _scoped(GroceryItem, request.user, pk)
    if item is None: return JsonResponse({'error': 'Not found.'}, status=404)
    item.delete(); return JsonResponse({'status': 'deleted'})

@login_required(login_url='login')
@require_POST
def save_suggestion_ajax(request):
    title = request.POST.get('title','').strip()
    body  = request.POST.get('body','').strip()
    cat   = request.POST.get('category','').strip()
    if not title: return JsonResponse({'error': 'Title required.'}, status=400)
    _, created = SavedSuggestion.objects.get_or_create(
        title=title, user=request.user, defaults={'body': body, 'category': cat})
    if created: return JsonResponse({'status': 'saved', 'message': f'"{title}" saved.'})
    return JsonResponse({'status': 'duplicate', 'message': f'"{title}" already saved.'})

@login_required(login_url='login')
@require_POST
def delete_suggestion_ajax(request, pk):
    s = _scoped(SavedSuggestion, request.user, pk)
    if s is None: return JsonResponse({'error': 'Not found.'}, status=404)
    s.delete(); return JsonResponse({'status': 'deleted'})

@login_required(login_url='login')
@require_POST
def budget_advisor_ajax(request):
    return budget_suggest_ajax(request)


# ── Smart Budget Insights AJAX ─────────────────────────────────────────────
# Returns recommendation engine output as JSON for the dashboard panel.

@login_required(login_url='login')
@require_POST
def budget_insights_ajax(request):
    """
    POST params:
        amount   – total allowance (required)
        period   – daily | weekly | monthly (default weekly)
        food, transportation, school_expenses, personal_needs,
        savings, emergency_fund, other  – planned allocations (all optional, default 0)
        savings_target  – desired savings amount (optional, default 0)

    Returns a JSON object with the full engine analysis result.
    """
    from apps.core.ai_engine import BudgetRecommendationEngine

    amount = _dec(request.POST.get('amount'))
    period = request.POST.get('period', 'weekly')
    if period not in ('daily', 'weekly', 'monthly'):
        period = 'weekly'

    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Enter an allowance amount greater than 0.'}, status=400)

    cats = ['food', 'transportation', 'school_expenses',
            'personal_needs', 'savings', 'emergency_fund', 'other']
    allocations = {}
    for cat in cats:
        v = _dec(request.POST.get(cat, '0'), Decimal('0'))
        allocations[cat] = max(Decimal('0'), v)

    savings_target = _dec(request.POST.get('savings_target', '0'), Decimal('0'))

    # Also pull in actual spending history for the current period
    today = timezone.now().date()
    ps, pe = _period_dates(period, today)
    history = _spending_by_cat(request.user, ps, pe)

    engine = BudgetRecommendationEngine(
        total_allowance=amount,
        period=period,
        allocations=allocations,
        spending_history=history,
        savings_target=max(Decimal('0'), savings_target),
    )
    result = engine.analyze()

    # Convert Decimal → float for JSON serialisation
    def _jsonify(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, dict):
            return {k: _jsonify(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_jsonify(x) for x in v]
        return v

    return JsonResponse(_jsonify(result))


# ── Dashboard AI Insights context helper ──────────────────────────────────

def _build_ai_insights(user, view_period: str, today) -> dict:
    """
    Build budget insights for the dashboard without a form submission.
    Uses the user's active budget plan if one exists, otherwise uses
    the total allowance with smart default splits.
    """
    from apps.core.ai_engine import BudgetRecommendationEngine

    total_allowance = (
        Allowance.objects.filter(user=user, is_deleted=False)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0')
    )

    # Try to find an active budget plan for the current period
    ps, pe = _period_dates(view_period, today)
    active_budget = (
        AllowanceBudget.objects
        .filter(user=user, period=view_period, period_start__lte=today, period_end__gte=today)
        .order_by('-created_at')
        .first()
    )

    if active_budget:
        allocations = active_budget.get_allocations()
        plan_amount = active_budget.total_amount
    else:
        plan_amount = total_allowance
        allocations = _suggest(plan_amount, view_period) if plan_amount > 0 else {}

    history = _spending_by_cat(user, ps, pe)

    if plan_amount == Decimal('0'):
        return {
            'has_insights': False,
            'no_allowance': True,
        }

    engine = BudgetRecommendationEngine(
        total_allowance=plan_amount,
        period=view_period,
        allocations=allocations,
        spending_history=history,
        savings_target=allocations.get('savings', Decimal('0')),
    )
    result = engine.analyze()
    result['has_insights'] = True
    result['no_allowance'] = False
    result['using_active_plan'] = bool(active_budget)
    result['period_start'] = ps
    result['period_end'] = pe
    return result


# ── Conversational Planner AJAX ────────────────────────────────────────────

@login_required(login_url='login')
@require_POST
def planner_advice_ajax(request):
    """
    POST params
    -----------
    intent : one of help_budget | daily_limit | save_more | afford_goal | review_spending
    amount : total allowance (optional override; defaults to user's total active allowance)
    period : daily | weekly | monthly (default weekly)
    food, transportation, school_expenses, personal_needs,
    savings, emergency_fund, other  (optional planned allocations)

    Returns JSON with keys: intent, message, follow_up, suggestions, breakdown
    """
    from apps.core.ai_engine import ConversationalPlanner, CATEGORY_LABELS

    user   = request.user
    intent = request.POST.get('intent', 'help_budget')
    period = request.POST.get('period', 'weekly')
    if period not in ('daily', 'weekly', 'monthly'):
        period = 'weekly'

    # Allowance total
    explicit_amount = _dec(request.POST.get('amount'))
    if explicit_amount and explicit_amount > 0:
        total = explicit_amount
    else:
        total = (
            Allowance.objects.filter(user=user, is_deleted=False)
            .aggregate(t=Sum('amount'))['t'] or Decimal('0')
        )

    # Available balance
    balance = _balance(user)

    # Planned allocations: from POST if present, else from active plan
    cats = list(CATEGORY_LABELS.keys())
    allocations = {}
    has_post_alloc = any(request.POST.get(f'alloc_{c}') for c in cats)
    if has_post_alloc:
        for cat in cats:
            v = _dec(request.POST.get(f'alloc_{cat}', '0'), Decimal('0'))
            allocations[cat] = max(Decimal('0'), v)
    else:
        today = timezone.now().date()
        ps, pe = _period_dates(period, today)
        active = (
            AllowanceBudget.objects
            .filter(user=user, period=period, period_start__lte=today, period_end__gte=today)
            .order_by('-created_at').first()
        )
        if active:
            allocations = active.get_allocations()

    # Spending history for the current period
    today  = timezone.now().date()
    ps, pe = _period_dates(period, today)
    history = _spending_by_cat(user, ps, pe)

    # Savings goals
    goals_data = []
    for g in SavingsGoal.objects.filter(user=user, status='in_progress'):
        dl_days = None
        if g.deadline:
            delta = (g.deadline - today).days
            dl_days = max(0, delta)
        goals_data.append({
            'name':          g.name,
            'target':        g.target_amount,
            'current':       g.current_amount,
            'deadline_days': dl_days,
        })

    planner = ConversationalPlanner(
        total_allowance=total,
        period=period,
        remaining_balance=balance,
        allocations=allocations,
        spending_history=history,
        savings_goals=goals_data,
    )

    response = planner.respond(intent)

    # Decimal → float for JSON
    def _jsonify(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, dict):
            return {k: _jsonify(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_jsonify(x) for x in v]
        return v

    return JsonResponse(_jsonify(response))


# ── Smart suggest with coaching message ────────────────────────────────────

@login_required(login_url='login')
@require_POST
def budget_suggest_smart_ajax(request):
    """
    Enhanced suggest endpoint that returns allocation splits PLUS a coaching
    intro message explaining the reasoning.

    POST params: amount, period
    Returns: same as budget_suggest_ajax PLUS 'coaching_message', 'reasons',
             'category_colors', 'category_icons'
    """
    from apps.core.ai_engine import (
        ConversationalPlanner, CATEGORY_LABELS,
        CATEGORY_ICONS, CATEGORY_COLORS,
    )

    amount = _dec(request.POST.get('amount'))
    period = request.POST.get('period', 'weekly')
    if period not in ('daily', 'weekly', 'monthly'):
        period = 'weekly'

    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Enter an amount greater than 0.'}, status=400)

    user    = request.user
    balance = _balance(user)
    today   = timezone.now().date()
    ps, pe  = _period_dates(period, today)
    history = _spending_by_cat(user, ps, pe)

    goals_data = []
    for g in SavingsGoal.objects.filter(user=user, status='in_progress'):
        dl_days = None
        if g.deadline:
            delta = (g.deadline - today).days
            dl_days = max(0, delta)
        goals_data.append({
            'name': g.name,
            'target': g.target_amount,
            'current': g.current_amount,
            'deadline_days': dl_days,
        })

    # Get GOAL-AWARE splits from the ConversationalPlanner._smart_split
    # instead of the fixed-percentage _suggest().
    planner = ConversationalPlanner(
        total_allowance=amount,
        period=period,
        remaining_balance=balance,
        allocations={},
        spending_history=history,
        savings_goals=goals_data,
    )
    splits_raw = planner._smart_split()   # goal-aware Decimal splits
    splits = {k: v for k, v in splits_raw.items()}

    # Build coaching message
    coaching = planner.respond('help_budget')

    # Build per-category reasons (goal-aware)
    reasons = {}
    for cat in CATEGORY_LABELS:
        v = splits.get(cat, Decimal('0'))
        # Enhance savings reason with goal context
        if cat == 'savings' and goals_data:
            active_goals = [g for g in goals_data
                            if Decimal(str(g.get('current', 0))) < Decimal(str(g.get('target', 0)))]
            if active_goals:
                g = active_goals[0]
                remaining_goal = max(Decimal('0'),
                    Decimal(str(g['target'])) - Decimal(str(g['current'])))
                pct = int(v / amount * 100) if amount > Decimal('0') else 0
                goal_note = (
                    f"Suggested ₱{v:.2f} ({pct}%) toward savings. "
                    f"Your goal '{g['name']}' needs ₱{remaining_goal:.2f} more. "
                )
                if g.get('deadline_days') is not None:
                    dl = g['deadline_days']
                    if dl == 0:
                        goal_note += "Deadline is today!"
                    else:
                        goal_note += f"Deadline in {dl} day{'s' if dl != 1 else ''}."
                else:
                    goal_note += "No deadline set — save at your own pace."
                reasons[cat] = goal_note
            else:
                reasons[cat] = planner._reason(cat, v, amount)
        else:
            reasons[cat] = planner._reason(cat, v, amount)

    result = {
        'total':   float(amount),
        'period':  period,
        'labels':  BUDGET_CAT_LABELS,
        'coaching_message': coaching.get('message', ''),
        'coaching_followup': coaching.get('follow_up', ''),
        'category_colors': CATEGORY_COLORS,
        'category_icons':  CATEGORY_ICONS,
        'reasons': reasons,
    }
    for cat, v in splits.items():
        result[cat] = float(v)

    return JsonResponse(result)
