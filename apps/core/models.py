from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


# ---------------------------------------------------------------------------
# New models for the updated Personal Budget Allowance System
# ---------------------------------------------------------------------------

class ExpenseItem(models.Model):
    """
    An item the user plans to buy.
    Adding it as 'planned' does NOT deduct money yet.
    Marking it 'purchased' triggers a single balance deduction (tracked by
    the balance_deducted flag to prevent double-counting).
    """
    CATEGORY_CHOICES = [
        ('food', 'Food'),
        ('transportation', 'Transportation'),
        ('school_supplies', 'School Supplies'),
        ('personal_needs', 'Personal Needs'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('planned', 'Planned'),
        ('purchased', 'Purchased'),
        ('cancelled', 'Cancelled'),
    ]

    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    quantity = models.PositiveIntegerField(default=1)
    price_per_item = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    total_cost = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='planned')
    date = models.DateField()
    # Prevents double-deduction if the row is saved twice with status=purchased
    balance_deducted = models.BooleanField(default=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='expense_items',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tblexpenseitem'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.name} – ₱{self.total_cost}'


class GoalContribution(models.Model):
    """A single monetary transfer from allowance into a SavingsGoal."""

    goal = models.ForeignKey(
        'SavingsGoal',
        on_delete=models.CASCADE,
        related_name='contributions',
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    note = models.CharField(max_length=255, blank=True)
    date = models.DateField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='goal_contributions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblgoalcontribution'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.goal.name} – ₱{self.amount}'


class SavedSuggestion(models.Model):
    """A budgeting suggestion the user explicitly saved."""

    title = models.CharField(max_length=255)
    body = models.TextField()
    category = models.CharField(max_length=50, blank=True)  # e.g. 'needs', 'wants', 'savings'
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_suggestions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblsavedsuggestion'
        # One copy of each suggestion title per user
        unique_together = ['title', 'user']
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Transaction(models.Model):
    """
    Immutable ledger entry for every balance-changing event.
    Written automatically by views — never edited.
    amount: positive = money in (allowance added, refund)
            negative = money out (purchase, savings transfer)
    """
    TYPE_CHOICES = [
        ('allowance_added', 'Allowance Added'),
        ('purchase', 'Purchase'),
        ('savings_transfer', 'Savings Transfer'),
        ('refund', 'Refund'),
    ]

    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.CharField(max_length=255)
    category = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='transactions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbltransaction'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.get_transaction_type_display()} – ₱{self.amount}'


# ---------------------------------------------------------------------------
# Original models (preserved)
# ---------------------------------------------------------------------------

class Category(models.Model):
    CATEGORY_TYPES = [
        ('need', 'Need'),
        ('want', 'Want'),
        ('saving', 'Saving'),
    ]

    name = models.CharField(max_length=100)
    category_type = models.CharField(max_length=10, choices=CATEGORY_TYPES, default='need')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='categories',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblcategory'
        ordering = ['name']
        unique_together = ['name', 'user']

    def __str__(self):
        return self.name


class Income(models.Model):
    SOURCE_TYPES = [
        ('allowance', 'Allowance'),
        ('part_time', 'Part-time Job'),
        ('gift', 'Gift'),
        ('other', 'Other'),
    ]

    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    source = models.CharField(max_length=20, choices=SOURCE_TYPES, default='allowance')
    description = models.CharField(max_length=255, blank=True)
    date = models.DateField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='incomes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblincome'
        ordering = ['-date']

    def __str__(self):
        return f"{self.source} - {self.amount}"


class Expense(models.Model):
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    description = models.CharField(max_length=255, blank=True)
    date = models.DateField()
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        related_name='expenses',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='expenses',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblexpense'
        ordering = ['-date']

    def __str__(self):
        return f"{self.category} - {self.amount}"


class BudgetPlan(models.Model):
    monthly_income = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='budget_plans',
    )
    allocated_amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    month = models.DateField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='budget_plans',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tblbudgetplan'
        unique_together = ['category', 'month', 'user']
        ordering = ['-month']

    def __str__(self):
        return f"{self.category} - {self.allocated_amount} ({self.month:%Y-%m})"


class SavingsGoal(models.Model):
    STATUS_CHOICES = [
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
    ]

    name = models.CharField(max_length=255)
    target_amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    current_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='in_progress')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='savings_goals',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tblsavingsgoal'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def progress_percentage(self):
        if self.target_amount == 0:
            return 0
        return min(100, (self.current_amount / self.target_amount) * 100)

    @property
    def remaining_amount(self):
        return max(Decimal('0'), self.target_amount - self.current_amount)

    def check_and_mark_completed(self):
        if self.current_amount >= self.target_amount and self.status != 'completed':
            self.status = 'completed'
            self.save(update_fields=['status', 'updated_at'])


class GroceryItem(models.Model):
    name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    estimated_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_purchased = models.BooleanField(default=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='grocery_items',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblgroceryitem'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Allowance(models.Model):
    ALLOWANCE_TYPES = [
        ('weekly', 'Weekly'),
        ('biweekly', 'Bi-weekly'),
        ('monthly', 'Monthly'),
    ]

    name = models.CharField(max_length=255, default='Weekly Allowance')
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    allowance_type = models.CharField(
        max_length=10,
        choices=ALLOWANCE_TYPES,
        default='weekly',
    )
    date_received = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='allowances',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblallowance'
        ordering = ['-date_received']

    def __str__(self):
        return f'{self.name} - {self.amount}'


class RecurringExpense(models.Model):
    name = models.CharField(max_length=255)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    is_active = models.BooleanField(default=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recurring_expenses',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblrecurringexpense'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} - {self.amount}'


class AllowanceBudget(models.Model):
    """
    A budget plan the user creates for a specific allowance and period.
    Saving a plan does NOT move money — it only records the user's intent.
    Allocations are stored as JSON: {'food': 300, 'transportation': 250, ...}
    """
    PERIOD_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]

    BUDGET_CATEGORIES = [
        'food',
        'transportation',
        'school_expenses',
        'personal_needs',
        'savings',
        'emergency_fund',
        'other',
    ]

    allowance = models.ForeignKey(
        Allowance,
        on_delete=models.CASCADE,
        related_name='budget_plans_v2',
        null=True,
        blank=True,
    )
    period = models.CharField(max_length=10, choices=PERIOD_CHOICES, default='weekly')
    # Date range this plan covers
    period_start = models.DateField()
    period_end = models.DateField()
    total_amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    # Allocations per category (stored individually for query flexibility)
    food = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    transportation = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    school_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    personal_needs = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    savings = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    emergency_fund = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    other = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    notes = models.TextField(blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='allowance_budgets',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tblallowancebudget'
        ordering = ['-period_start']

    def __str__(self):
        return f'{self.get_period_display()} budget ₱{self.total_amount} ({self.period_start})'

    @property
    def total_allocated(self):
        return (
            self.food + self.transportation + self.school_expenses +
            self.personal_needs + self.savings + self.emergency_fund + self.other
        )

    @property
    def unallocated(self):
        return max(Decimal('0'), self.total_amount - self.total_allocated)

    def get_allocations(self):
        """Return a dict of category → allocated amount."""
        return {
            'food': self.food,
            'transportation': self.transportation,
            'school_expenses': self.school_expenses,
            'personal_needs': self.personal_needs,
            'savings': self.savings,
            'emergency_fund': self.emergency_fund,
            'other': self.other,
        }

    def daily_amount(self):
        """For weekly/monthly plans, compute daily equivalent."""
        if self.period == 'daily':
            return self.total_amount
        if self.period == 'weekly':
            return (self.total_amount / Decimal('7')).quantize(Decimal('0.01'))
        # monthly: approx 30 days
        return (self.total_amount / Decimal('30')).quantize(Decimal('0.01'))
