from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.info.models import Info


class InfoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='infouser',
            password='infopassword123',
            email='info@example.com'
        )
        self.client.force_login(self.user)

    def test_info_list_page(self):
        response = self.client.get(reverse('info:info_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'info.html')

    def test_info_ajax_crud(self):
        # Save AJAX
        save_resp = self.client.post(reverse('info:info_save_ajax'), {
            'name': 'Juan Dela Cruz',
            'age': '21',
            'address': 'Manila, Philippines',
            'email': 'juan@example.com',
        })
        self.assertEqual(save_resp.status_code, 200)
        info_id = save_resp.json()['id']
        self.assertTrue(Info.objects.filter(id=info_id, user=self.user).exists())

        # List AJAX
        list_resp = self.client.get(reverse('info:info_list_ajax'))
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()['data']), 1)

        # Get AJAX
        get_resp = self.client.get(reverse('info:info_get_ajax', args=[info_id]))
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()['name'], 'Juan Dela Cruz')

        # Delete AJAX
        del_resp = self.client.post(reverse('info:info_delete_ajax', args=[info_id]))
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(Info.objects.filter(id=info_id).exists())
