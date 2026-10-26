from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from apps.core.models import (
Allowance,
BudgetPlan,
Category,
Expense,
GroceryItem,
Income,
RecurringExpense,
SavingsGoal,
)
User = get_user_model()

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION = timedelta(minutes=1)
INVALID_LOGIN_MESSAGE = 'Invalid username or password.'
LOCKED_MESSAGE = 'Too many failed attempts. Locked for 1 minute.'


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


@login_required(login_url='login')
def home(request):
    user = request.user

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_income':
            Income.objects.create(
                amount=request.POST.get('amount', 0),
                source=request.POST.get('source', 'allowance'),
                description=request.POST.get('description', ''),
                date=request.POST.get('date'),
                user=user,
            )
            messages.success(request, 'Income added.')

        elif action == 'add_expense':
            category = Category.objects.filter(id=request.POST.get('category'), user=user).first()
            Expense.objects.create(
                amount=request.POST.get('amount', 0),
                description=request.POST.get('description', ''),
                date=request.POST.get('date'),
                category=category,
                user=user,
            )
            messages.success(request, 'Expense added.')

        elif action == 'add_grocery':
            GroceryItem.objects.create(
                name=request.POST.get('name', ''),
                quantity=request.POST.get('quantity', 1),
                estimated_price=request.POST.get('estimated_price', 0),
                user=user,
            )
            messages.success(request, 'Grocery item added.')

        elif action == 'toggle_grocery':
            item = GroceryItem.objects.filter(id=request.POST.get('id'), user=user).first()
            if item:
                item.is_purchased = not item.is_purchased
                item.save()

        elif action == 'delete_grocery':
            GroceryItem.objects.filter(id=request.POST.get('id'), user=user).delete()

        return redirect('dashboard')

    categories = Category.objects.filter(user=user)
    incomes = Income.objects.filter(user=user)
    expenses = Expense.objects.filter(user=user)
    budget_plans = BudgetPlan.objects.filter(user=user)
    savings_goals = SavingsGoal.objects.filter(user=user)
    grocery_items = GroceryItem.objects.filter(user=user)
    recurring_expenses = RecurringExpense.objects.filter(user=user, is_active=True)
    allowances = user.allowances.order_by('-date_received')

    # Calculate weekly allowance total
    from django.utils import timezone
    import datetime
    today = timezone.now().date()
    start_of_week = today - datetime.timedelta(today.weekday())  # Monday
    end_of_week = start_of_week + datetime.timedelta(days=6)  # Sunday
    weekly_allowance = sum(
        a.amount for a in allowances
        if start_of_week <= a.date_received <= end_of_week
    )

    total_income = sum(i.amount for i in incomes)
    total_expenses = sum(e.amount for e in expenses)
    balance = total_income - total_expenses
    total_recurring = sum(r.amount for r in recurring_expenses)

    # Budget vs actual tracking
    budget_utilization = {}
    for plan in budget_plans:
        category_expenses = expenses.filter(category=plan.category)
        spent = sum(e.amount for e in category_expenses)
        allocated = plan.allocated_amount
        if allocated > 0:
            budget_utilization[plan.id] = min(100, (spent / allocated) * 100)
        else:
            budget_utilization[plan.id] = 0

    # Monthly income and expenses summary
    from datetime import datetime, timedelta
    today = datetime.now().date()
    first_of_month = today.replace(day=1)
    previous_month = (first_of_month - timedelta(days=1)).replace(day=1)

    monthly_income = sum(
        i.amount for i in incomes if i.date >= first_of_month
    )
    monthly_expenses = sum(
        e.amount for e in expenses if e.date >= first_of_month
    )
    previous_month_expenses = sum(
        e.amount for e in expenses if e.date >= previous_month and e.date < first_of_month
    )

    recent_expenses = expenses.order_by('-date')[:5]
    recent_incomes = incomes.order_by('-date')[:5]

    # Calculate expenses by category for doughnut chart
    from collections import defaultdict
    expenses_by_category = defaultdict(float)
    food_expenses = 0
    for exp in expenses:
        if exp.category:
            expenses_by_category[exp.category.name] += exp.amount
        if exp.category and exp.category.category_type == 'need':
            food_expenses += exp.amount

    expenses_by_category_name = list(expenses_by_category.keys())
    expenses_by_category_amount = list(expenses_by_category.values())

    # Calculate weekly allowance data for bar chart (last 8 weeks)
    from django.db.models import Sum
    from django.utils import timezone
    import datetime

    weekly_allowance_data = []
    weekly_expense_data = []
    weekly_data_labels = []

    today = timezone.now().date()
    for i in range(7, -1, -1):
        week_start = today - datetime.timedelta(days=today.weekday() + i*7)
        week_end = week_start + datetime.timedelta(days=6)
        label = week_start.strftime('%m/%d')

        # Weekly allowance for this period
        weekly_allowance_obj = allowances.filter(
            date_received__gte=week_start,
            date_received__lte=week_end
        ).aggregate(total=Sum('amount'))['total'] or 0
        weekly_allowance_data.append(float(weekly_allowance_obj))

        # Weekly expenses for this period
        weekly_exp_obj = expenses.filter(
            date__gte=week_start,
            date__lte=week_end
        ).aggregate(total=Sum('amount'))['total'] or 0
        weekly_expense_data.append(float(weekly_exp_obj))

        weekly_data_labels.append(label)

    # Recent allowance records (last 5)
    recent_allowances = user.allowances.order_by('-date_received')[:5]

    # Calculate savings goals totals
    total_saved = 0
    total_target = 0
    for goal in savings_goals:
        total_saved += float(goal.current_amount)
        total_target += float(goal.target_amount)
    if total_target > 0:
        savings_pct = round(total_saved / total_target * 100)
    else:
        savings_pct = 0

    context = {
        'categories': categories,
        'incomes': incomes,
        'expenses': expenses,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'balance': balance,
        'budget_plans': budget_plans,
        'savings_goals': savings_goals,
        'recent_expenses': recent_expenses,
        'recent_incomes': recent_incomes,
        'grocery_items': grocery_items,
        'total_recurring': total_recurring,
        'budget_utilization': budget_utilization,
        'monthly_income': monthly_income,
        'monthly_expenses': monthly_expenses,
        'previous_month_expenses': previous_month_expenses,
        'weekly_allowance': weekly_allowance,
        'expenses_by_category_name': expenses_by_category_name,
        'expenses_by_category_amount': expenses_by_category_amount,
        'food_expenses': food_expenses,
        'recent_allowances': recent_allowances,
        'weekly_allowance_data': weekly_allowance_data,
        'weekly_expense_data': weekly_expense_data,
        'weekly_data_labels': weekly_data_labels,
        'savings_total_saved': total_saved,
        'savings_total_target': total_target,
        'savings_pct': savings_pct,
    }
    return render(request, 'home.html', context)


@login_required(login_url='login')
def my_allowance(request):
    """Display and manage the user's allowance records."""
    user = request.user
    allowances = user.allowances.order_by('-date_received')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_allowance':
            Allowance.objects.create(
                name=request.POST.get('name', 'Weekly Allowance'),
                amount=request.POST.get('amount', 0),
                allowance_type=request.POST.get('allowance_type', 'weekly'),
                date_received=request.POST.get('date_received'),
                description=request.POST.get('description', ''),
                user=user,
            )
            messages.success(request, 'Allowance added.')
            return redirect('my_allowance')

    # Calculate weekly allowance total for the selected week
    from django.db.models import Sum
    from django.utils import timezone
    import datetime

    # Get the current week's allowance
    today = timezone.now().date()
    start_of_week = today - datetime.timedelta(today.weekday())  # Monday
    end_of_week = start_of_week + datetime.timedelta(days=6)  # Sunday

    weekly_allowance = allowances.filter(
        date_received__gte=start_of_week,
        date_received__lte=end_of_week
    ).aggregate(total=Sum('amount'))['total'] or 0

    context = {
        'allowances': allowances,
        'weekly_allowance': weekly_allowance,
        'today': today,
        'start_of_week': start_of_week,
        'end_of_week': end_of_week,
    }
    return render(request, 'my_allowance.html', context)


@login_required(login_url='login')
def expenses_page(request):
    """Display and manage expenses."""
    user = request.user
    expenses = user.expenses.order_by('-date')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_expense':
            category = Category.objects.filter(id=request.POST.get('category'), user=user).first()
            Expense.objects.create(
                amount=request.POST.get('amount', 0),
                description=request.POST.get('description', ''),
                date=request.POST.get('date'),
                category=category,
                user=user,
            )
            messages.success(request, 'Expense added.')
            return redirect('expenses')

    categories = Category.objects.filter(user=user)
    context = {
        'expenses': expenses,
        'categories': categories,
    }
    return render(request, 'expenses.html', context)


@login_required(login_url='login')
def budget_planner_page(request):
    """Budget planner interface."""
    user = request.user
    categories = Category.objects.filter(user=user)
    budget_plans = BudgetPlan.objects.filter(user=user).order_by('-month')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_budget':
            category = Category.objects.filter(id=request.POST.get('category'), user=user).first()
            if category is None:
                messages.error(request, 'Please select a valid category.')
                return redirect('budget_planner')

            monthly_income = _to_decimal(request.POST.get('monthly_income'))
            allocated_amount = _to_decimal(request.POST.get('allocated_amount'))
            if monthly_income is None or monthly_income < 0:
                messages.error(request, 'Monthly income must be 0 or more.')
                return redirect('budget_planner')
            if allocated_amount is None or allocated_amount < 0:
                messages.error(request, 'Allocated amount must be 0 or more.')
                return redirect('budget_planner')

            month = _to_month(request.POST.get('month'))
            if month is None:
                messages.error(request, 'A valid month is required.')
                return redirect('budget_planner')

            plan, created = BudgetPlan.objects.get_or_create(
                category=category,
                month=month,
                user=user,
                defaults={
                    'monthly_income': monthly_income,
                    'allocated_amount': allocated_amount,
                }
            )
            if not created:
                plan.monthly_income = monthly_income
                plan.allocated_amount = allocated_amount
                plan.save()
            messages.success(request, f'Budget plan {"created" if created else "updated"} successfully.')
            return redirect('budget_planner')

    context = {
        'categories': categories,
        'budget_plans': budget_plans,
    }
    return render(request, 'budget_planner.html', context)


@login_required(login_url='login')
def savings_goals_page(request):
    """Display and manage savings goals with contribution functionality."""
    user = request.user
    savings_goals = user.savings_goals.order_by('-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_goal':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Goal name is required.')
                return redirect('savings_goals')

            target_amount = _to_decimal(request.POST.get('target_amount'))
            current_amount = _to_decimal(request.POST.get('current_amount'), Decimal('0'))
            if target_amount is None or target_amount <= 0:
                messages.error(request, 'Target amount must be greater than 0.')
                return redirect('savings_goals')
            if current_amount is None or current_amount < 0:
                messages.error(request, 'Current amount must be 0 or more.')
                return redirect('savings_goals')

            deadline = request.POST.get('deadline', '').strip()
            if deadline:
                deadline = _to_date(deadline)
                if deadline is None:
                    messages.error(request, 'A valid deadline is required.')
                    return redirect('savings_goals')
            else:
                deadline = None

            SavingsGoal.objects.create(
                name=name,
                target_amount=target_amount,
                current_amount=current_amount,
                deadline=deadline,
                user=user,
            )
            messages.success(request, 'Savings goal added.')
            return redirect('savings_goals')
        elif action == 'add_contribution':
            goal_id = request.POST.get('goal_id')
            goal = SavingsGoal.objects.filter(id=goal_id, user=user).first()
            if goal is None:
                messages.error(request, 'Savings goal not found.')
                return redirect('savings_goals')

            contribution_amount = _to_decimal(request.POST.get('contribution_amount'))
            if contribution_amount is None or contribution_amount <= 0:
                messages.error(request, 'Contribution amount must be greater than 0.')
                return redirect('savings_goals')

            goal.current_amount = Decimal(str(goal.current_amount)) + contribution_amount
            goal.save()
            messages.success(request, f'Contribution of ₱{contribution_amount:.2f} added.')
            return redirect('savings_goals')

    context = {
        'savings_goals': savings_goals,
    }
    return render(request, 'savings_goals.html', context)


@login_required(login_url='login')
def food_list_page(request):
    """Display and manage the food/grocery list."""
    user = request.user
    grocery_items = user.grocery_items.order_by('-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_grocery':
            GroceryItem.objects.create(
                name=request.POST.get('name', ''),
                quantity=request.POST.get('quantity', 1),
                estimated_price=request.POST.get('estimated_price', 0),
                user=user,
            )
            messages.success(request, 'Grocery item added.')
            return redirect('food_list')

    context = {
        'grocery_items': grocery_items,
    }
    return render(request, 'food_list.html', context)


@login_required(login_url='login')
def reports_page(request):
    """Display financial reports."""
    user = request.user
    incomes = user.incomes.order_by('-date')
    expenses = user.expenses.order_by('-date')
    savings_goals = user.savings_goals.order_by('-created_at')
    budget_plans = user.budget_plans.order_by('-month')

    from datetime import datetime, timedelta
    from django.db.models import Sum

    today = timezone.now().date()
    first_of_month = today.replace(day=1)
    previous_month = (first_of_month - timedelta(days=1)).replace(day=1)

    monthly_income = sum(i.amount for i in incomes if i.date >= first_of_month)
    monthly_expenses = sum(e.amount for e in expenses if e.date >= first_of_month)
    previous_month_expenses = sum(e.amount for e in expenses if e.date >= previous_month and e.date < first_of_month)

    total_income = sum(i.amount for i in incomes)
    total_expenses = sum(e.amount for e in expenses)
    balance = total_income - total_expenses

    # Expenses by category
    from apps.core.models import Category
    category_stats = []
    categories = Category.objects.filter(user=user)
    for cat in categories:
        cat_expenses = expenses.filter(category=cat)
        cat_spent = sum(e.amount for e in cat_expenses)
        category_stats.append({
            'name': cat.name,
            'type': cat.category_type,
            'spent': cat_spent,
        })

    context = {
        'incomes': incomes,
        'expenses': expenses,
        'savings_goals': savings_goals,
        'budget_plans': budget_plans,
        'monthly_income': monthly_income,
        'monthly_expenses': monthly_expenses,
        'previous_month_expenses': previous_month_expenses,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'balance': balance,
        'category_stats': category_stats,
    }
    return render(request, 'reports.html', context)


@login_required(login_url='login')
def profile_page(request):
    """User profile page."""
    user = request.user

    if request.method == 'POST':
        # Handle profile update
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

    context = {
        'user': user,
    }
    return render(request, 'profile.html', context)


def _to_decimal(value, default=None):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _to_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _to_month(value):
    for fmt in ('%Y-%m', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date().replace(day=1)
        except (TypeError, ValueError):
            continue
    return None


BUDGET_RULE = [
    ('need', 'Needs', Decimal('0.50'), 'Essentials: food, fare, school supplies, and bills.'),
    ('want', 'Wants', Decimal('0.30'), 'Non-essentials: eating out, load/data, games, and extras.'),
    ('saving', 'Savings', Decimal('0.20'), 'Set this aside for your goals and emergencies.'),
]


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





def _scoped_or_none(model, user, pk):
    return model.objects.filter(id=pk, user=user).first()


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
    messages.success(request, 'Income has been deleted successfully.')
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
    messages.success(request, 'Expense has been deleted successfully.')
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
    valid_types = [choice[0] for choice in Category.CATEGORY_TYPES]
    if category_type not in valid_types:
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
    messages.success(request, 'Category has been deleted successfully.')
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
    messages.success(request, 'Budget plan has been deleted successfully.')
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
    if deadline:
        deadline = _to_date(deadline)
        if deadline is None:
            return JsonResponse({'error': 'A valid deadline is required.'}, status=400)
    else:
        deadline = None

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
    messages.success(request, 'Savings goal has been deleted successfully.')
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

    quantity = request.POST.get('quantity', '1')
    try:
        quantity = int(quantity)
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
    messages.success(request, 'Grocery item has been deleted successfully.')
    return JsonResponse({'status': 'deleted'})


BUDGET_RULE = [
    ('need', 'Needs', Decimal('0.50'), 'Essentials: food, fare, school supplies, and bills.'),
    ('want', 'Wants', Decimal('0.30'), 'Non-essentials: eating out, load/data, games, and extras.'),
    ('saving', 'Savings', Decimal('0.20'), 'Set this aside for your goals and emergencies.'),
]

BUDGET_EXAMPLES = {
    'need': [
        ('Food & groceries', 'Rice, ulam, veggies, snacks at home'),
        ('Fare / transportation', 'Jeep, bus, tricycle, fuel'),
        ('School supplies', 'Notebooks, pens, printing, projects'),
        ('Load / data', 'Internet, mobile data, subscriptions for school'),
        ('Bills / rent', 'Dorm, electricity, water, shared bills'),
    ],
    'want': [
        ('Snacks & milk tea', 'Milk tea, coffee, chips, desserts'),
        ('Eating out', 'Fast food, restaurants, food delivery'),
        ('Games / apps', 'In-app purchases, game credits, streaming'),
        ('Hobbies', 'Sports, art, music, collections'),
        ('Clothes & accessories', 'Outfits, shoes, bags'),
    ],
    'saving': [
        ('Emergency fund', 'For unexpected expenses'),
        ('Savings goal', 'Laptop, phone, travel, gadget'),
        ('Tuition / project fund', 'Next term, thesis, group projects'),
    ],
}

FOOD_EXAMPLES = [
    ('Rice / staple', Decimal('0.40')),
    ('Ulam / viands', Decimal('0.35')),
    ('Snacks', Decimal('0.15')),
    ('Drinks / water', Decimal('0.10')),
]


@login_required(login_url='login')
@require_POST
def budget_advisor_ajax(request):
    amount = _to_decimal(request.POST.get('amount'))
    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Enter an amount greater than 0.'}, status=400)

    categories = list(Category.objects.filter(user=request.user))
    grocery_items = GroceryItem.objects.filter(user=request.user)
    expenses = Expense.objects.filter(user=request.user)
    recurring_expenses = RecurringExpense.objects.filter(user=request.user, is_active=True)

    grocery_planned = sum(
        (item.estimated_price or Decimal('0')) * item.quantity for item in grocery_items
    )

    actual_expenses = sum(e.amount for e in expenses)
    recurring_monthly = sum(r.amount for r in recurring_expenses)

    buckets = []
    bucket_amounts = {}
    for type_key, label, pct, tip in BUDGET_RULE:
        bucket_amount = (amount * pct).quantize(Decimal('0.01'))
        bucket_amounts[type_key] = bucket_amount
        type_categories = [c for c in categories if c.category_type == type_key]
        per_category = (
            (bucket_amount / len(type_categories)).quantize(Decimal('0.01'))
            if type_categories else None
        )
        buckets.append({
            'type': type_key,
            'label': label,
            'percent': int(pct * 100),
            'amount': float(bucket_amount),
            'tip': tip,
            'examples': [
                {'name': name, 'note': note} for name, note in BUDGET_EXAMPLES[type_key]
            ],
            'categories': [
                {'name': c.name, 'amount': float(per_category)} for c in type_categories
            ],
        })

    needs_amount = bucket_amounts['need']

    food_budget = (needs_amount * Decimal('0.60')).quantize(Decimal('0.01'))
    food_items = [
        {
            'name': name,
            'percent': int(pct * 100),
            'amount': float((food_budget * pct).quantize(Decimal('0.01'))),
        }
        for name, pct in FOOD_EXAMPLES
    ]

    tips = ['Follow the 50/30/20 rule: 50% needs, 30% wants, 20% savings.']
    if grocery_planned > 0:
        if grocery_planned > needs_amount:
            tips.append(
                f'Your grocery list costs about ₱{grocery_planned:.2f}, which is more than your '
                f'₱{needs_amount:.2f} needs budget. Trim the list or move some to wants.'
            )
        else:
            tips.append(
                f'Your grocery list costs about ₱{grocery_planned:.2f} — within your '
                f'₱{needs_amount:.2f} needs budget.'
            )
            tips.append(
                f'After groceries, about ₱{needs_amount - grocery_planned:.2f} is left for other '
                f'needs (fare, supplies).'
            )
    else:
        tips.append('Add items to your grocery list so we can estimate your food budget.')

    tips.append('Priority order: needs first, then savings, and wants last.')

    effective_amount = amount - recurring_monthly

    return JsonResponse({
        'amount': float(amount),
        'buckets': buckets,
        'grocery_planned': float(grocery_planned),
        'needs_amount': float(needs_amount),
        'food': {'budget': float(food_budget), 'items': food_items},
        'tips': tips,
        'actual_expenses': float(actual_expenses),
        'recurring_expenses': float(recurring_monthly),
        'effective_amount': float(effective_amount),
    })


@login_required(login_url='login')
@require_POST
def allowance_save_ajax(request):
    pk = request.POST.get('id')
    if pk:
        allowance = _scoped_or_none(Allowance, request.user, pk)
        if allowance is None:
            return JsonResponse({'error': 'Allowance record not found.'}, status=404)
    else:
        allowance = Allowance()

    name = request.POST.get('name', 'Weekly Allowance').strip()
    if not name:
        name = 'Weekly Allowance'

    amount = _to_decimal(request.POST.get('amount'))
    if amount is None or amount <= 0:
        return JsonResponse({'error': 'Amount must be greater than 0.'}, status=400)

    allowance_type = request.POST.get('allowance_type', 'weekly')
    valid_types = [c[0] for c in Allowance.ALLOWANCE_TYPES]
    if allowance_type not in valid_types:
        allowance_type = 'weekly'

    date_str = request.POST.get('date_received') or request.POST.get('date')
    date_val = _to_date(date_str)
    if date_val is None:
        return JsonResponse({'error': 'A valid date is required.'}, status=400)

    allowance.name = name
    allowance.amount = amount
    allowance.allowance_type = allowance_type
    allowance.date_received = date_val
    allowance.description = request.POST.get('description', '').strip()
    if not allowance.pk:
        allowance.user = request.user
    allowance.save()

    messages.success(request, f'Allowance has been {"updated" if pk else "added"} successfully.')
    return JsonResponse({'status': 'success', 'id': allowance.id})


@login_required(login_url='login')
@require_POST
def allowance_delete_ajax(request, pk):
    allowance = _scoped_or_none(Allowance, request.user, pk)
    if allowance is None:
        return JsonResponse({'error': 'Allowance record not found.'}, status=404)
    allowance.delete()
    messages.success(request, 'Allowance record has been deleted successfully.')
    return JsonResponse({'status': 'deleted'})
