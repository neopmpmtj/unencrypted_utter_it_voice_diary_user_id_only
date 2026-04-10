"""
Tests for src/invoice_parser/tasks.py
"""

from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from src.accounts.models import UserSecret
from src.ingestion.models import IngestItem
from src.financial_parser.models import FinancialRecord

from src.invoice_parser.tasks import process_invoices_for_all_users_task

User = get_user_model()


class ProcessInvoicesForAllUsersTaskTestCase(TestCase):
    """Test process_invoices_for_all_users_task runs for users with Gmail."""

    def setUp(self):
        self.user_with_gmail = User.objects.create_user(
            email="gmail_user@example.com",
            password="testpass123",
        )
        self.user_with_gmail.save()

        self.user_without_gmail = User.objects.create_user(
            email="no_gmail@example.com",
            password="testpass123",
        )
        self.user_without_gmail.save()

        us_gmail = UserSecret.objects.create(user=self.user_with_gmail)
        us_gmail.set_scopes_list(["https://www.googleapis.com/auth/gmail.modify"])
        us_gmail.save()

        us_no_gmail = UserSecret.objects.create(user=self.user_without_gmail)
        us_no_gmail.set_scopes_list(["https://www.googleapis.com/auth/drive"])
        us_no_gmail.save()

    @patch("src.invoice_parser.tasks.process_invoice_messages")
    def test_calls_process_for_users_with_gmail(self, mock_process):
        mock_process.return_value = {"summary": {"pdfs_parsed": 0}, "errors": []}

        process_invoices_for_all_users_task()

        mock_process.assert_called_once()
        call_user = mock_process.call_args[0][0]
        self.assertEqual(call_user.email, "gmail_user@example.com")

    @patch("src.invoice_parser.pdf_parser.services.log_api_usage")
    @patch("src.invoice_parser.pdf_parser.services.add_label_to_message")
    @patch("src.invoice_parser.pdf_parser.services.parse_pdf_invoice")
    @patch("src.invoice_parser.pdf_parser.services.get_pdf_attachments")
    @patch("src.invoice_parser.pdf_parser.services.message_has_attachments")
    @patch("src.invoice_parser.pdf_parser.services.search_inbox_messages")
    @patch("src.invoice_parser.pdf_parser.services.get_or_create_label")
    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    def test_celery_pipeline_creates_ingest_item_when_mocked_gmail_returns_pdf(
        self,
        mock_verify,
        mock_get_label,
        mock_search,
        mock_has_att,
        mock_get_pdfs,
        mock_parse,
        mock_add_label,
        mock_log,
    ):
        """Celery Beat pipeline: when process_invoice_messages runs with mocked Gmail/OpenAI,
        persist_invoice_to_db must create IngestItem and FinancialRecord."""
        mock_verify.return_value = True
        mock_get_label.return_value = "Label_123"
        mock_search.return_value = ["msg_celery_test"]
        mock_has_att.return_value = True
        mock_get_pdfs.return_value = [
            {"filename": "invoice.pdf", "data": b"pdf", "mime_type": "application/pdf"}
        ]
        mock_parse.return_value = {
            "parsed": {
                "vendor_name": "Celery Test Vendor",
                "invoice_number": "INV-CELERY",
                "invoice_date": "2026-03-18",
                "currency": "EUR",
                "line_items": [
                    {"description": "Item A", "quantity": 1, "unit_price": 5.00, "total": 5.00},
                ],
                "total_amount": 5.00,
            },
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }

        initial_count = IngestItem.objects.filter(provider="gmail").count()

        process_invoices_for_all_users_task()

        created = IngestItem.objects.filter(provider="gmail").count() - initial_count
        self.assertGreaterEqual(
            created,
            1,
            "Celery pipeline must create at least one IngestItem when mocked Gmail returns a PDF",
        )
        item = IngestItem.objects.filter(provider="gmail").order_by("-ingested_at").first()
        self.assertIsNotNone(item)
        self.assertEqual(item.external_id, "msg_celery_test")
        self.assertEqual(item.source_filename, "invoice.pdf")
        fr = FinancialRecord.objects.filter(source_item=item).first()
        self.assertIsNotNone(fr, "FinancialRecord must exist for created IngestItem")
        self.assertEqual(fr.record_name, "Celery Test Vendor")
