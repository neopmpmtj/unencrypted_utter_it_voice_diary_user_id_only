"""Tests for ckh_invoices.check_invoice_emails_in_inbox and query construction."""

from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from src.accounts.models import UserSecret
from src.gmail_parsers.ckh_invoices.config import TRIGGER_WORDS
from src.gmail_parsers.ckh_invoices.services import check_invoice_emails_in_inbox

User = get_user_model()


class CheckInvoiceEmailsInInboxTests(TestCase):
    """Test check_invoice_emails_in_inbox returns correct dict."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="invoice@example.com",
            password="Pass123",
        )

    def test_no_gmail_permission_returns_both_false(self):
        UserSecret.objects.create(user=self.user)
        result = check_invoice_emails_in_inbox(self.user)
        self.assertEqual(result["messages"], [])

    @patch("src.gmail_parsers.ckh_invoices.services.search_inbox_messages")
    @patch("src.gmail_parsers.ckh_invoices.services.verify_gmail_permissions")
    def test_gmail_permission_empty_result_returns_both_false(self, mock_verify, mock_search):
        mock_verify.return_value = True
        mock_search.return_value = []

        result = check_invoice_emails_in_inbox(self.user)

        self.assertEqual(result["messages"], [])
        mock_search.assert_called_once_with(self.user, mock_search.call_args[0][1], max_results=20)

    @patch("src.gmail_parsers.ckh_invoices.services.message_has_attachments")
    @patch("src.gmail_parsers.ckh_invoices.services.search_inbox_messages")
    @patch("src.gmail_parsers.ckh_invoices.services.verify_gmail_permissions")
    def test_invoices_without_attachment(self, mock_verify, mock_search, mock_has_att):
        mock_verify.return_value = True
        mock_search.return_value = ["msg123"]
        mock_has_att.return_value = False

        result = check_invoice_emails_in_inbox(self.user)

        self.assertEqual(result["messages"], [{"id": "msg123", "has_attachment": False}])

    @patch("src.gmail_parsers.ckh_invoices.services.message_has_attachments")
    @patch("src.gmail_parsers.ckh_invoices.services.search_inbox_messages")
    @patch("src.gmail_parsers.ckh_invoices.services.verify_gmail_permissions")
    def test_invoices_with_attachment(self, mock_verify, mock_search, mock_has_att):
        mock_verify.return_value = True
        mock_search.return_value = ["msg123"]
        mock_has_att.return_value = True

        result = check_invoice_emails_in_inbox(self.user)

        self.assertEqual(result["messages"], [{"id": "msg123", "has_attachment": True}])

    @patch("src.gmail_parsers.ckh_invoices.services.message_has_attachments")
    @patch("src.gmail_parsers.ckh_invoices.services.search_inbox_messages")
    @patch("src.gmail_parsers.ckh_invoices.services.verify_gmail_permissions")
    def test_multiple_messages_mixed_attachments(self, mock_verify, mock_search, mock_has_att):
        mock_verify.return_value = True
        mock_search.return_value = ["msg1", "msg2"]
        mock_has_att.side_effect = [False, True]

        result = check_invoice_emails_in_inbox(self.user)

        self.assertEqual(
            result["messages"],
            [{"id": "msg1", "has_attachment": False}, {"id": "msg2", "has_attachment": True}],
        )

    @patch("src.gmail_parsers.ckh_invoices.services.message_has_attachments")
    @patch("src.gmail_parsers.ckh_invoices.services.search_inbox_messages")
    @patch("src.gmail_parsers.ckh_invoices.services.verify_gmail_permissions")
    def test_query_includes_english_and_portuguese_trigger_words(
        self, mock_verify, mock_search, mock_has_att
    ):
        mock_verify.return_value = True
        mock_search.return_value = []
        mock_has_att.return_value = False

        check_invoice_emails_in_inbox(self.user)

        query = mock_search.call_args[0][1]
        for word in TRIGGER_WORDS:
            self.assertIn(f"subject:{word}", query)
        self.assertIn("invoice", query)
        self.assertIn("fatura", query)
        self.assertIn("recibo", query)
