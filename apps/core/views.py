from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

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
    from apps.core.models import BudgetPlan, Category, Expense, GroceryItem, Income, SavingsGoal

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
    grocery_items = GroceryItem.objects.filter(user=user, is_purchased=False)

    total_income = sum(i.amount for i in incomes)
    total_expenses = sum(e.amount for e in expenses)
    balance = total_income - total_expenses

    recent_expenses = expenses.order_by('-date')[:5]
    recent_incomes = incomes.order_by('-date')[:5]

    context = {
        'categories': categories,
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
