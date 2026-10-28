from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# ActivityLog — immutable audit trail for every financial event
# ---------------------------------------------------------------------------

class ActivityLog(models.Model):
    """
    Immutable, append-only record of every financial activity.
    Never update or delete rows in this table.
    balance_before / balance_after are always computed from _compute_balance()
    at the moment the event occurs.
    """
    ACTIVITY_TYPES = [
        # Allowance
        ('allowance_added',    'Allowance Added'),
        ('allowance_deleted',  'Allowance Record Deleted'),
        ('allowance_adjusted', 'Allowance Adjusted'),
        # Expenses
        ('expense_planned',    'Expense Planned'),
        ('expense_purchased',  'Purchase Completed'),
        ('expense_cancelled',  'Expense Cancelled'),
        ('expense_deleted',    'Expense Record Deleted'),
        ('refund',             'Refund / Reversal'),
        # Savings
        ('savings_transfer',   'Savings Transfer'),
        ('savings_returned',   'Savings Returned'),
        ('goal_deleted',       'Goal Deleted'),
        # Generic
        ('correction',         'Financial Correction'),
        ('budget_saved',       'Budget Plan Saved'),
    ]

    STATUS_CHOICES = [
        ('completed',  'Completed'),
        ('reversed',   'Reversed'),
        ('deleted',    'Deleted'),
        ('archived',   'Archived'),
    ]

    activity_type    = models.CharField(max_length=30, choices=ACTIVITY_TYPES)
    description      = models.CharField(max_length=512)
    category         = models.CharField(max_length=100, blank=True)
    amount           = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Positive = money in, negative = money out, null = non-monetary event',
    )
    balance_before   = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after    = models.DecimalField(max_digits=10, decimal_places=2)
    status           = models.CharField(max_length=12, choices=STATUS_CHOICES, default='completed')
    reason           = models.TextField(blank=True, help_text='Reason for correction / deletion')
    # Link back to source objects (nullable — may be gone after soft-delete)
    related_allowance_id   = models.IntegerField(null=True, blank=True)
    related_expense_id     = models.IntegerField(null=True, blank=True)
    # Snapshot of original description / amount (for deleted records)
    original_description   = models.CharField(max_length=512, blank=True)
    original_amount        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    date             = models.DateField()
    timestamp        = models.DateTimeField(default=timezone.now)
    user             = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='activity_logs',
    )

    class Meta:
        db_table  = 'tblactivitylog'
        ordering  = ['-timestamp']
        # Enforce immutability at DB level if possible (covered by views only)

    def __str__(self):
        return f'[{self.get_activity_type_display()}] {self.description} ({self.date})'


# ---------------------------------------------------------------------------
# ExpenseItem
# ---------------------------------------------------------------------------

class ExpenseItem(models.Model):
    CATEGORY_CHOICES = [
        ('food',            'Food'),
        ('transportation',  'Transportation'),
        ('school_expenses', 'School Expenses'),
        ('personal_needs',  'Personal Needs'),
        ('savings',         'Savings'),
        ('emergency_fund',  'Emergency Fund'),
        ('other',           'Other'),
    ]
    STATUS_CHOICES = [
        ('planned',   'Planned'),
        ('purchased', 'Purchased'),
        ('cancelled', 'Cancelled'),
    ]

    name           = models.CharField(max_length=255)
    category       = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    quantity       = models.PositiveIntegerField(default=1)
    price_per_item = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    total_cost     = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default='planned')
    date           = models.DateField()
    balance_deducted = models.BooleanField(default=False)
    user           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='expense_items')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tblexpenseitem'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.name} – ₱{self.total_cost}'


# ---------------------------------------------------------------------------
# GoalContribution
# ---------------------------------------------------------------------------

class GoalContribution(models.Model):
    goal   = models.ForeignKey('SavingsGoal', on_delete=models.CASCADE, related_name='contributions')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    note   = models.CharField(max_length=255, blank=True)
    date   = models.DateField()
    user   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='goal_contributions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblgoalcontribution'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.goal.name} – ₱{self.amount}'


# ---------------------------------------------------------------------------
# SavedSuggestion
# ---------------------------------------------------------------------------

class SavedSuggestion(models.Model):
    title    = models.CharField(max_length=255)
    body     = models.TextField()
    category = models.CharField(max_length=50, blank=True)
    user     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_suggestions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblsavedsuggestion'
        unique_together = ['title', 'user']
        ordering = ['-created_at']

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Transaction  (immutable ledger — used by dashboard/list)
# ---------------------------------------------------------------------------

class Transaction(models.Model):
    TYPE_CHOICES = [
        ('allowance_added',   'Allowance Added'),
        ('allowance_deleted', 'Allowance Deleted'),
        ('purchase',          'Purchase'),
        ('expense_planned',   'Expense Planned'),
        ('savings_transfer',  'Savings Transfer'),
        ('refund',            'Refund'),
        ('correction',        'Correction'),
    ]

    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description      = models.CharField(max_length=255)
    category         = models.CharField(max_length=100, blank=True)
    amount           = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after    = models.DecimalField(max_digits=10, decimal_places=2)
    date             = models.DateField()
    user             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transactions')
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbltransaction'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.get_transaction_type_display()} – ₱{self.amount}'


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------

class Category(models.Model):
    CATEGORY_TYPES = [('need', 'Need'), ('want', 'Want'), ('saving', 'Saving')]
    name          = models.CharField(max_length=100)
    category_type = models.CharField(max_length=10, choices=CATEGORY_TYPES, default='need')
    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='categories')
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblcategory'
        ordering = ['name']
        unique_together = ['name', 'user']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Income  (legacy — kept for backward compat)
# ---------------------------------------------------------------------------

class Income(models.Model):
    SOURCE_TYPES = [('allowance','Allowance'),('part_time','Part-time Job'),('gift','Gift'),('other','Other')]
    amount      = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    source      = models.CharField(max_length=20, choices=SOURCE_TYPES, default='allowance')
    description = models.CharField(max_length=255, blank=True)
    date        = models.DateField()
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='incomes')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblincome'
        ordering = ['-date']

    def __str__(self):
        return f'{self.source} - {self.amount}'


# ---------------------------------------------------------------------------
# Expense (legacy)
# ---------------------------------------------------------------------------

class Expense(models.Model):
    amount      = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    description = models.CharField(max_length=255, blank=True)
    date        = models.DateField()
    category    = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='expenses')
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='expenses')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblexpense'
        ordering = ['-date']

    def __str__(self):
        return f'{self.category} - {self.amount}'


# ---------------------------------------------------------------------------
# BudgetPlan (legacy)
# ---------------------------------------------------------------------------

class BudgetPlan(models.Model):
    monthly_income   = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    category         = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='budget_plans')
    allocated_amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    month            = models.DateField()
    user             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='budget_plans')
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tblbudgetplan'
        unique_together = ['category', 'month', 'user']
        ordering = ['-month']

    def __str__(self):
        return f'{self.category} - {self.allocated_amount} ({self.month:%Y-%m})'


# ---------------------------------------------------------------------------
# SavingsGoal
# ---------------------------------------------------------------------------

class SavingsGoal(models.Model):
    STATUS_CHOICES = [('in_progress','In Progress'),('completed','Completed')]
    name           = models.CharField(max_length=255)
    target_amount  = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    current_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    deadline       = models.DateField(null=True, blank=True)
    status         = models.CharField(max_length=12, choices=STATUS_CHOICES, default='in_progress')
    user           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='savings_goals')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

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


# ---------------------------------------------------------------------------
# GroceryItem (legacy)
# ---------------------------------------------------------------------------

class GroceryItem(models.Model):
    name            = models.CharField(max_length=255)
    quantity        = models.PositiveIntegerField(default=1)
    estimated_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_purchased    = models.BooleanField(default=False)
    user            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='grocery_items')
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblgroceryitem'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Allowance  — with soft-delete support
# ---------------------------------------------------------------------------

class Allowance(models.Model):
    ALLOWANCE_TYPES = [
        ('daily',    'Daily'),
        ('weekly',   'Weekly'),
        ('biweekly', 'Bi-weekly'),
        ('monthly',  'Monthly'),
    ]

    name             = models.CharField(max_length=255, default='Weekly Allowance')
    amount           = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    allowance_type   = models.CharField(max_length=10, choices=ALLOWANCE_TYPES, default='weekly')
    date_received    = models.DateField()
    description      = models.CharField(max_length=255, blank=True)
    # Soft-delete fields
    is_deleted       = models.BooleanField(default=False, db_index=True)
    deleted_at       = models.DateTimeField(null=True, blank=True)
    deletion_reason  = models.CharField(max_length=255, blank=True)
    user             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='allowances')
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblallowance'
        ordering = ['-date_received', '-created_at']

    def __str__(self):
        prefix = '[DELETED] ' if self.is_deleted else ''
        return f'{prefix}{self.name} - {self.amount}'

    def soft_delete(self, reason=''):
        """Mark as deleted without removing from DB."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deletion_reason = reason
        self.save(update_fields=['is_deleted', 'deleted_at', 'deletion_reason'])


# ---------------------------------------------------------------------------
# RecurringExpense
# ---------------------------------------------------------------------------

class RecurringExpense(models.Model):
    name       = models.CharField(max_length=255)
    amount     = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    is_active  = models.BooleanField(default=True)
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recurring_expenses')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tblrecurringexpense'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} - {self.amount}'


# ---------------------------------------------------------------------------
# AllowanceBudget
# ---------------------------------------------------------------------------

class AllowanceBudget(models.Model):
    PERIOD_CHOICES = [
        ('daily',   'Daily'),
        ('weekly',  'Weekly'),
        ('monthly', 'Monthly'),
    ]
    BUDGET_CATEGORIES = [
        'food', 'transportation', 'school_expenses',
        'personal_needs', 'savings', 'emergency_fund', 'other',
    ]

    allowance     = models.ForeignKey(Allowance, on_delete=models.CASCADE, related_name='budget_plans_v2', null=True, blank=True)
    name          = models.CharField(max_length=120, blank=True, default='',
                                     help_text='Optional user-assigned plan name')
    period        = models.CharField(max_length=10, choices=PERIOD_CHOICES, default='weekly')
    period_start  = models.DateField()
    period_end    = models.DateField()
    total_amount  = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    food          = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    transportation= models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    school_expenses=models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    personal_needs= models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    savings       = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    emergency_fund= models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    other         = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    notes         = models.TextField(blank=True)
    is_archived   = models.BooleanField(default=False, db_index=True,
                                        help_text='Archived plans are hidden from the active planner')
    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='allowance_budgets')
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tblallowancebudget'
        ordering = ['-period_start']

    def __str__(self):
        label = self.name or f'{self.get_period_display()} budget'
        return f'{label} ₱{self.total_amount} ({self.period_start})'

    def display_name(self):
        """Human-readable plan name for templates."""
        if self.name:
            return self.name
        return f'{self.get_period_display()} Plan – {self.period_start.strftime("%b %d, %Y")}'

    @property
    def total_allocated(self):
        return (self.food + self.transportation + self.school_expenses +
                self.personal_needs + self.savings + self.emergency_fund + self.other)

    @property
    def unallocated(self):
        return max(Decimal('0'), self.total_amount - self.total_allocated)

    def get_allocations(self):
        return {
            'food': self.food, 'transportation': self.transportation,
            'school_expenses': self.school_expenses, 'personal_needs': self.personal_needs,
            'savings': self.savings, 'emergency_fund': self.emergency_fund, 'other': self.other,
        }

    def daily_amount(self):
        if self.period == 'daily':
            return self.total_amount
        if self.period == 'weekly':
            return (self.total_amount / Decimal('7')).quantize(Decimal('0.01'))
        return (self.total_amount / Decimal('30')).quantize(Decimal('0.01'))
