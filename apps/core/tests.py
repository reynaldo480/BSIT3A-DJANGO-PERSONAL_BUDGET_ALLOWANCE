import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.models import (
    Allowance,
    BudgetPlan,
    Category,
    Expense,
    GroceryItem,
    Income,
    SavingsGoal,
)

User = get_user_model()


class CoreViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='secretpassword123',
            email='test@example.com'
        )
        self.client.force_login(self.user)

    def test_dashboard_renders(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')

    def test_dashboard_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_my_allowance_page_and_creation(self):
        # GET page
        response = self.client.get(reverse('my_allowance'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'my_allowance.html')

        # POST add_allowance
        post_data = {
            'action': 'add_allowance',
            'name': 'Weekly Test Allowance',
            'amount': '500.00',
            'allowance_type': 'weekly',
            'date_received': '2026-10-26',
            'description': 'From parents',
        }
        post_response = self.client.post(reverse('my_allowance'), post_data)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(Allowance.objects.filter(user=self.user).count(), 1)

    def test_allowance_ajax_save_and_delete(self):
        # Create via AJAX
        save_data = {
            'name': 'Monthly Allowance',
            'amount': '2000.00',
            'allowance_type': 'monthly',
            'date_received': '2026-10-01',
            'description': 'Monthly stipend',
        }
        save_resp = self.client.post(reverse('allowance_save_ajax'), save_data)
        self.assertEqual(save_resp.status_code, 200)
        allowance_id = save_resp.json()['id']
        self.assertTrue(Allowance.objects.filter(id=allowance_id, user=self.user).exists())

        # Delete via AJAX
        del_resp = self.client.post(reverse('allowance_delete_ajax', args=[allowance_id]))
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(Allowance.objects.filter(id=allowance_id).exists())

    def test_expenses_page_and_creation(self):
        category = Category.objects.create(name='Food', category_type='need', user=self.user)
        response = self.client.get(reverse('expenses'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'expenses.html')

        post_data = {
            'action': 'add_expense',
            'amount': '150.00',
            'description': 'Lunch',
            'date': '2026-10-26',
            'category': str(category.id),
        }
        post_response = self.client.post(reverse('expenses'), post_data)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)
        # Verify related_name='expenses' works on user
        self.assertEqual(self.user.expenses.count(), 1)

    def test_budget_planner_page(self):
        category = Category.objects.create(name='Tuition', category_type='need', user=self.user)
        response = self.client.get(reverse('budget_planner'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'budget_planner.html')

        post_data = {
            'action': 'add_budget',
            'category': str(category.id),
            'monthly_income': '10000.00',
            'allocated_amount': '5000.00',
            'month': '2026-10',
        }
        post_response = self.client.post(reverse('budget_planner'), post_data)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(BudgetPlan.objects.filter(user=self.user).count(), 1)

    def test_savings_goals_page_and_contribution(self):
        goal = SavingsGoal.objects.create(
            name='Emergency Fund',
            target_amount=Decimal('1000.00'),
            current_amount=Decimal('200.00'),
            user=self.user,
        )
        response = self.client.get(reverse('savings_goals'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'savings_goals.html')

        # Test remaining_amount property
        self.assertEqual(goal.remaining_amount, Decimal('800.00'))

        # Test add_contribution (tests the previously broken f-string floatformat)
        contrib_data = {
            'action': 'add_contribution',
            'goal_id': str(goal.id),
            'contribution_amount': '100.50',
        }
        contrib_response = self.client.post(reverse('savings_goals'), contrib_data)
        self.assertEqual(contrib_response.status_code, 302)
        goal.refresh_from_db()
        self.assertEqual(goal.current_amount, Decimal('300.50'))

    def test_food_list_page(self):
        response = self.client.get(reverse('food_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'food_list.html')

        post_data = {
            'action': 'add_grocery',
            'name': 'Rice (5kg)',
            'quantity': '1',
            'estimated_price': '260.00',
        }
        post_response = self.client.post(reverse('food_list'), post_data)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(GroceryItem.objects.filter(user=self.user).count(), 1)

    def test_reports_page(self):
        response = self.client.get(reverse('reports'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'reports.html')

    def test_profile_page(self):
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'profile.html')

        post_data = {
            'first_name': 'Juan',
            'last_name': 'Dela Cruz',
            'email': 'juan@example.com',
        }
        post_response = self.client.post(reverse('profile'), post_data)
        self.assertEqual(post_response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Juan')
        self.assertEqual(self.user.last_name, 'Dela Cruz')
