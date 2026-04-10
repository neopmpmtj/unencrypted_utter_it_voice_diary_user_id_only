"""Tests for GIGO views."""

from django.test import TestCase, Client
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences
from src.gigo.models import GigoUserState


class DismissViewTests(TestCase):
    """Tests for the dismiss API endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="gigo@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_dismiss_requires_login(self):
        response = self.client.post(reverse("gigo:dismiss"))
        self.assertEqual(response.status_code, 302)

    def test_dismiss_clears_alert(self):
        GigoUserState.objects.create(
            user=self.user,
            consecutive_low_count=3,
            alert_pending=True,
        )
        self.client.login(email="gigo@example.com", password="Pass123")
        response = self.client.post(
            reverse("gigo:dismiss"),
            content_type="application/json",
            data={},
        )
        self.assertEqual(response.status_code, 200)
        state = GigoUserState.objects.get(user=self.user)
        self.assertFalse(state.alert_pending)
