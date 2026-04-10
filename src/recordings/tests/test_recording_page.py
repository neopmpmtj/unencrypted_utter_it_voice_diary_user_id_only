"""
Tests for recording page view - showTimer from user preferences.
"""

import json
import re
from django.test import TestCase, Client
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences


class RecordingPageTimerTests(TestCase):
    """Tests for recording page showTimer based on user preferences."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('recordings:record')
        self.user = CustomUser.objects.create_user(
            email='timer@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()
        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def _get_recorder_config(self, response):
        """Extract recorder_config JSON from the page's script tag."""
        match = re.search(r'<script[^>]*id="recorder-config"[^>]*>(.*?)</script>', response.content.decode(), re.DOTALL)
        self.assertIsNotNone(match)
        return json.loads(match.group(1).strip())

    def test_recording_page_show_timer_true_when_preference_enabled(self):
        """recording_page should include showTimer: true when user has show_recording_timer=True."""
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.show_recording_timer = True
        prefs.save()

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        config = self._get_recorder_config(response)
        self.assertTrue(config.get('showTimer'))

    def test_recording_page_show_timer_false_when_preference_disabled(self):
        """recording_page should include showTimer: false when user has show_recording_timer=False."""
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.show_recording_timer = False
        prefs.save()

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        config = self._get_recorder_config(response)
        self.assertFalse(config.get('showTimer'))

    def test_recording_page_show_timer_default_true_for_new_user(self):
        """recording_page should include showTimer: true when UserPreferences has default (True)."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        config = self._get_recorder_config(response)
        self.assertTrue(config.get('showTimer'))

    def test_recording_page_is_app_admin_false_for_regular_user(self):
        """recorder_config should include isAppAdmin: false for non-admin users.
        Spinner overlay hides when green (processing) for regular users."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        config = self._get_recorder_config(response)
        self.assertFalse(config.get('isAppAdmin', True))

    def test_recording_page_is_app_admin_true_for_app_admin_user(self):
        """recorder_config should include isAppAdmin: true for app_admin users.
        Spinner overlay stays visible when green (processing) for app admins."""
        self.user.is_app_admin = True
        self.user.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        config = self._get_recorder_config(response)
        self.assertTrue(config.get('isAppAdmin'))
