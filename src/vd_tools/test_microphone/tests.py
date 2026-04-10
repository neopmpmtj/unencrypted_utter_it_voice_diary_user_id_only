"""
Tests for microphone test tool.
"""

from django.test import TestCase, Client
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences


class MicrophoneTestViewTests(TestCase):
    """Tests for the microphone test view."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('test_microphone:microphone_test')
        self.user = CustomUser.objects.create_user(
            email='mic@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.interface_language = 'en'
        prefs.save()

    def test_microphone_test_requires_login(self):
        """microphone_test should redirect to login when not authenticated."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_microphone_test_returns_200_when_authenticated(self):
        """microphone_test should return 200 when user is logged in."""
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_microphone_test_page_contains_start_record_button(self):
        """Page should contain Start Record button."""
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'Start Record')

    def test_microphone_test_page_contains_countdown_element(self):
        """Page should contain countdown element for recording."""
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'micTestCountdown')

    def test_microphone_test_page_contains_record_again_button(self):
        """Page should contain Record Again button after recording."""
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'Record Again')
