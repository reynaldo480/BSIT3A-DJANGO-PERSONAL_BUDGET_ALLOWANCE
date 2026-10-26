from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class UserManagementTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='adminuser',
            password='adminpassword123',
            email='admin@example.com'
        )
        self.regular_user = User.objects.create_user(
            username='regularuser',
            password='regularpassword123',
            email='regular@example.com'
        )

    def test_user_list_superuser_access(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('users:user_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'users/list.html')

    def test_user_list_non_superuser_redirect(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse('users:user_list'))
        self.assertEqual(response.status_code, 302)

    def test_user_ajax_operations(self):
        self.client.force_login(self.superuser)

        # List AJAX
        list_resp = self.client.get(reverse('users:user_list_ajax'))
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertIn('data', data)

        # Save AJAX (create new)
        save_resp = self.client.post(reverse('users:user_save_ajax'), {
            'username': 'newuser1',
            'first_name': 'New',
            'last_name': 'One',
            'email': 'new1@example.com',
            'password': 'somepassword123',
            'is_active': 'true',
            'is_staff': 'false',
        })
        self.assertEqual(save_resp.status_code, 200)
        new_id = save_resp.json()['id']
        self.assertTrue(User.objects.filter(id=new_id).exists())

        # Get AJAX
        get_resp = self.client.get(reverse('users:user_get_ajax', args=[new_id]))
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()['username'], 'newuser1')

        # Delete AJAX
        del_resp = self.client.post(reverse('users:user_delete_ajax', args=[new_id]))
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(User.objects.filter(id=new_id).exists())
