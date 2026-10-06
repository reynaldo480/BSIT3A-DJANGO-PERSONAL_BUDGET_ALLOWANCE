from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


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
    name = models.CharField(max_length=255)
    target_amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    current_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    deadline = models.DateField(null=True, blank=True)
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
