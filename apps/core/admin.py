from django.contrib import admin
from .models import (
    Category,
    Income,
    Expense,
    BudgetPlan,
    SavingsGoal,
    GroceryItem,
    Allowance,
    RecurringExpense,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category_type', 'user', 'created_at')
    list_filter = ('category_type', 'created_at')
    search_fields = ('name', 'user__username')


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ('source', 'amount', 'date', 'user', 'created_at')
    list_filter = ('source', 'date')
    search_fields = ('description', 'user__username')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('category', 'amount', 'date', 'user', 'created_at')
    list_filter = ('category', 'date')
    search_fields = ('description', 'user__username')


@admin.register(BudgetPlan)
class BudgetPlanAdmin(admin.ModelAdmin):
    list_display = ('category', 'month', 'monthly_income', 'allocated_amount', 'user')
    list_filter = ('month',)
    search_fields = ('category__name', 'user__username')


@admin.register(SavingsGoal)
class SavingsGoalAdmin(admin.ModelAdmin):
    list_display = ('name', 'target_amount', 'current_amount', 'deadline', 'user', 'created_at')
    list_filter = ('deadline', 'created_at')
    search_fields = ('name', 'user__username')


@admin.register(GroceryItem)
class GroceryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'quantity', 'estimated_price', 'is_purchased', 'user', 'created_at')
    list_filter = ('is_purchased', 'created_at')
    search_fields = ('name', 'user__username')


@admin.register(Allowance)
class AllowanceAdmin(admin.ModelAdmin):
    list_display = ('name', 'amount', 'allowance_type', 'date_received', 'user', 'created_at')
    list_filter = ('allowance_type', 'date_received')
    search_fields = ('name', 'description', 'user__username')


@admin.register(RecurringExpense)
class RecurringExpenseAdmin(admin.ModelAdmin):
    list_display = ('name', 'amount', 'is_active', 'user', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'user__username')
