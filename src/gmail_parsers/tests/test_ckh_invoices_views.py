"""Tests for ckh_invoices API view."""

from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences


class InvoicesCheckApiTests(TestCase):
    """Tests for GET /gmail-parsers/api/invoices/check/."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="invoice@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_requires_login(self):
        response = self.client.get(reverse("gmail_parsers:invoices_check"))
        self.assertEqual(response.status_code, 302)

    @patch("src.gmail_parsers.ckh_invoices.views.verify_gmail_permissions")
    def test_no_gmail_permission_returns_403(self, mock_verify):
        mock_verify.return_value = False
        self.client.login(email="invoice@example.com", password="Pass123")

        response = self.client.get(reverse("gmail_parsers:invoices_check"))

        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertEqual(data["error"], "gmail_not_connected")

    @patch("src.gmail_parsers.ckh_invoices.views.check_invoice_emails_in_inbox")
    @patch("src.gmail_parsers.ckh_invoices.views.verify_gmail_permissions")
    def test_has_invoices_with_attachment(self, mock_verify, mock_check):
        mock_verify.return_value = True
        mock_check.return_value = {
            "messages": [{"id": "msg1", "has_attachment": True}],
        }
        self.client.login(email="invoice@example.com", password="Pass123")

        response = self.client.get(reverse("gmail_parsers:invoices_check"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["messages"], [{"id": "msg1", "has_attachment": True}])

    @patch("src.gmail_parsers.ckh_invoices.views.check_invoice_emails_in_inbox")
    @patch("src.gmail_parsers.ckh_invoices.views.verify_gmail_permissions")
    def test_no_invoices_returns_both_false(self, mock_verify, mock_check):
        mock_verify.return_value = True
        mock_check.return_value = {"messages": []}
        self.client.login(email="invoice@example.com", password="Pass123")

        response = self.client.get(reverse("gmail_parsers:invoices_check"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["messages"], [])
