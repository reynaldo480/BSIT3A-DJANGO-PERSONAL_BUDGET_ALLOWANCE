from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('home/', views.home, name='dashboard'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),

    path('income/save/', views.income_save_ajax, name='income_save_ajax'),
    path('income/delete/<int:pk>/', views.income_delete_ajax, name='income_delete_ajax'),
    path('expense/save/', views.expense_save_ajax, name='expense_save_ajax'),
    path('expense/delete/<int:pk>/', views.expense_delete_ajax, name='expense_delete_ajax'),
    path('category/save/', views.category_save_ajax, name='category_save_ajax'),
    path('category/delete/<int:pk>/', views.category_delete_ajax, name='category_delete_ajax'),
    path('budget/save/', views.budget_save_ajax, name='budget_save_ajax'),
    path('budget/delete/<int:pk>/', views.budget_delete_ajax, name='budget_delete_ajax'),
    path('savings/save/', views.savings_save_ajax, name='savings_save_ajax'),
    path('savings/delete/<int:pk>/', views.savings_delete_ajax, name='savings_delete_ajax'),
    path('grocery/save/', views.grocery_save_ajax, name='grocery_save_ajax'),
    path('grocery/toggle/<int:pk>/', views.grocery_toggle_ajax, name='grocery_toggle_ajax'),
    path('grocery/delete/<int:pk>/', views.grocery_delete_ajax, name='grocery_delete_ajax'),
]