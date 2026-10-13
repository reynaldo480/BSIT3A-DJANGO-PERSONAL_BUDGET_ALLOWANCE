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

from apps.core.models import BudgetPlan, Category, Expense, GroceryItem, Income, SavingsGoal

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

    total_income = sum(i.amount for i in incomes)
    total_expenses = sum(e.amount for e in expenses)
    balance = total_income - total_expenses

    recent_expenses = expenses.order_by('-date')[:5]
    recent_incomes = incomes.order_by('-date')[:5]

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
    }
    return render(request, 'home.html', context)


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

    grocery_planned = sum(
        (item.estimated_price or Decimal('0')) * item.quantity for item in grocery_items
    )

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

    return JsonResponse({
        'amount': float(amount),
        'buckets': buckets,
        'grocery_planned': float(grocery_planned),
        'needs_amount': float(needs_amount),
        'food': {'budget': float(food_budget), 'items': food_items},
        'tips': tips,
    })
