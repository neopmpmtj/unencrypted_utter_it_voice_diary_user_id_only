"""
Tests for src/invoice_parser/pdf_parser/services.py
"""

import json
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.contrib.auth import get_user_model

from src.invoice_parser.pdf_parser.services import (
    parse_pdf_invoice,
    process_invoice_messages,
    _build_invoice_query,
)

User = get_user_model()


class BuildInvoiceQueryTestCase(TestCase):
    """Test _build_invoice_query() builds correct Gmail search query."""

    def test_query_includes_trigger_words(self):
        query = _build_invoice_query()
        self.assertIn("subject:", query)
        self.assertIn(" OR ", query)
        self.assertIn("invoice", query.lower())

    def test_build_invoice_query_excludes_processed_label(self):
        query = _build_invoice_query()
        self.assertIn("-label:UtterIt/InvoiceParsed", query)


class ParsePdfInvoiceTestCase(TestCase):
    """Test parse_pdf_invoice() function."""

    @patch("src.invoice_parser.pdf_parser.services._get_openai_client")
    def test_parse_pdf_invoice_success(self, mock_get_client):
        invoice_json = {
            "vendor_name": "Test Corp",
            "invoice_number": "INV-001",
            "invoice_date": "2026-01-15",
            "due_date": "2026-02-15",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Consulting",
                    "quantity": 10.0,
                    "unit_price": 100.0,
                    "total": 1000.0,
                }
            ],
            "subtotal": 1000.0,
            "tax": 230.0,
            "total_amount": 1230.0,
        }

        mock_usage = MagicMock()
        mock_usage.input_tokens = 500
        mock_usage.output_tokens = 200
        mock_usage.total_tokens = 700

        mock_response = MagicMock()
        mock_response.output_text = json.dumps(invoice_json)
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.responses.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = parse_pdf_invoice(b"%PDF-1.4 fake", "test.pdf")

        self.assertEqual(result["parsed"]["vendor_name"], "Test Corp")
        self.assertEqual(result["parsed"]["total_amount"], 1230.0)
        self.assertEqual(result["usage"]["input_tokens"], 500)
        self.assertEqual(result["usage"]["output_tokens"], 200)
        self.assertEqual(result["usage"]["total_tokens"], 700)

    @patch("src.invoice_parser.pdf_parser.services._get_openai_client")
    def test_parse_pdf_invoice_strips_markdown_fences(self, mock_get_client):
        invoice_json = {"vendor_name": "Fenced Corp", "total_amount": 99.0}

        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.total_tokens = 150

        mock_response = MagicMock()
        mock_response.output_text = f"```json\n{json.dumps(invoice_json)}\n```"
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.responses.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = parse_pdf_invoice(b"%PDF-1.4 fake", "fenced.pdf")

        self.assertEqual(result["parsed"]["vendor_name"], "Fenced Corp")

    @patch("src.invoice_parser.pdf_parser.services._get_openai_client")
    def test_parse_pdf_invoice_invalid_json(self, mock_get_client):
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.total_tokens = 150

        mock_response = MagicMock()
        mock_response.output_text = "This is not JSON at all"
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.responses.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = parse_pdf_invoice(b"%PDF-1.4 fake", "bad.pdf")

        self.assertIn("error", result["parsed"])
        self.assertIn("invalid JSON", result["parsed"]["error"])


class ProcessInvoiceMessagesTestCase(TestCase):
    """Test process_invoice_messages() full pipeline."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="invoice_test@example.com",
            password="testpass123",
        )
        self.user.save()

    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    def test_no_gmail_permission(self, mock_verify):
        mock_verify.return_value = False
        result = process_invoice_messages(self.user)
        self.assertEqual(result["results"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("Gmail permissions", result["errors"][0])

    @patch("src.invoice_parser.pdf_parser.services.get_or_create_label")
    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    def test_returns_early_when_label_setup_fails(self, mock_verify, mock_get_label):
        mock_verify.return_value = True
        mock_get_label.side_effect = Exception("Network error")
        result = process_invoice_messages(self.user)
        self.assertEqual(result["results"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("Label setup failed", result["errors"][0])
        self.assertEqual(result["summary"]["messages_found"], 0)
        self.assertEqual(result["summary"]["pdfs_parsed"], 0)

    @patch("src.invoice_parser.pdf_parser.services.get_or_create_label")
    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    @patch("src.invoice_parser.pdf_parser.services.search_inbox_messages")
    def test_no_messages_found(self, mock_search, mock_verify, mock_get_label):
        mock_verify.return_value = True
        mock_get_label.return_value = "Label_123"
        mock_search.return_value = []
        result = process_invoice_messages(self.user)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["summary"]["messages_found"], 0)

    @patch("src.invoice_parser.pdf_parser.services.get_or_create_label")
    @patch("src.invoice_parser.pdf_parser.services.parse_pdf_invoice")
    @patch("src.invoice_parser.pdf_parser.services.get_pdf_attachments")
    @patch("src.invoice_parser.pdf_parser.services.message_has_attachments")
    @patch("src.invoice_parser.pdf_parser.services.search_inbox_messages")
    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    def test_full_pipeline(self, mock_verify, mock_search, mock_has_att, mock_get_pdfs, mock_parse, mock_get_label):
        mock_verify.return_value = True
        mock_get_label.return_value = "Label_123"
        mock_search.return_value = ["msg1", "msg2"]
        mock_has_att.side_effect = [True, False]
        mock_get_pdfs.return_value = [
            {"filename": "invoice.pdf", "data": b"pdf-data", "mime_type": "application/pdf"}
        ]
        mock_parse.return_value = {
            "parsed": {"vendor_name": "Test", "total_amount": 100.0},
            "usage": {"input_tokens": 500, "output_tokens": 200, "total_tokens": 700},
        }

        result = process_invoice_messages(self.user)

        self.assertEqual(result["summary"]["messages_found"], 2)
        self.assertEqual(result["summary"]["pdfs_parsed"], 1)
        self.assertEqual(result["summary"]["ingest_items_created"], 1)
        self.assertEqual(result["summary"]["ingest_items_skipped"], 0)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["message_id"], "msg1")
        self.assertEqual(result["results"][0]["filename"], "invoice.pdf")
        self.assertEqual(result["results"][0]["parsed"]["vendor_name"], "Test")

    @patch("src.invoice_parser.pdf_parser.services.get_or_create_label")
    @patch("src.invoice_parser.pdf_parser.services.message_has_attachments")
    @patch("src.invoice_parser.pdf_parser.services.search_inbox_messages")
    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    def test_message_without_attachments_skipped(self, mock_verify, mock_search, mock_has_att, mock_get_label):
        mock_verify.return_value = True
        mock_get_label.return_value = "Label_123"
        mock_search.return_value = ["msg1"]
        mock_has_att.return_value = False

        result = process_invoice_messages(self.user)

        self.assertEqual(result["summary"]["messages_found"], 1)
        self.assertEqual(result["summary"]["pdfs_parsed"], 0)
        self.assertEqual(result["results"], [])

    @patch("src.invoice_parser.pdf_parser.services.get_or_create_label")
    @patch("src.invoice_parser.pdf_parser.services.log_api_usage")
    @patch("src.invoice_parser.pdf_parser.services.persist_invoice_to_db")
    @patch("src.invoice_parser.pdf_parser.services.parse_pdf_invoice")
    @patch("src.invoice_parser.pdf_parser.services.get_pdf_attachments")
    @patch("src.invoice_parser.pdf_parser.services.message_has_attachments")
    @patch("src.invoice_parser.pdf_parser.services.search_inbox_messages")
    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    def test_logs_api_usage(
        self, mock_verify, mock_search, mock_has_att, mock_get_pdfs, mock_parse,
        mock_persist, mock_log, mock_get_label,
    ):
        mock_verify.return_value = True
        mock_get_label.return_value = "Label_123"
        mock_search.return_value = ["msg1"]
        mock_has_att.return_value = True
        mock_get_pdfs.return_value = [
            {"filename": "inv.pdf", "data": b"x", "mime_type": "application/pdf"}
        ]
        mock_parse.return_value = {
            "parsed": {"vendor_name": "Test", "total_amount": 10.0},
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }
        mock_persist.return_value = None

        result = process_invoice_messages(self.user)

        gmail_calls = [c for c in mock_log.call_args_list if len(c[0]) > 1 and c[0][1] == "gmail"]
        self.assertGreater(len(gmail_calls), 0)
        self.assertEqual(gmail_calls[0][0][2], "gmail_messages_read")
        self.assertEqual(gmail_calls[0][0][3], 1)

        token_calls = [c for c in mock_log.call_args_list if len(c[0]) > 2 and c[0][2] in ("input_tokens", "output_tokens")]
        self.assertGreaterEqual(len(token_calls), 2)
        self.assertEqual(result["summary"]["ingest_items_created"], 0)
        self.assertEqual(result["summary"]["ingest_items_skipped"], 1)
        self.assertEqual(len(result["errors"]), 1)

    @patch("src.invoice_parser.pdf_parser.services.add_label_to_message")
    @patch("src.invoice_parser.pdf_parser.services.get_or_create_label")
    @patch("src.invoice_parser.pdf_parser.services.log_api_usage")
    @patch("src.invoice_parser.pdf_parser.services.persist_invoice_to_db")
    @patch("src.invoice_parser.pdf_parser.services.parse_pdf_invoice")
    @patch("src.invoice_parser.pdf_parser.services.get_pdf_attachments")
    @patch("src.invoice_parser.pdf_parser.services.message_has_attachments")
    @patch("src.invoice_parser.pdf_parser.services.search_inbox_messages")
    @patch("src.invoice_parser.pdf_parser.services.verify_gmail_permissions")
    def test_process_invoice_messages_adds_label_on_success(
        self, mock_verify, mock_search, mock_has_att, mock_get_pdfs, mock_parse,
        mock_persist, mock_log, mock_get_label, mock_add_label,
    ):
        mock_verify.return_value = True
        mock_search.return_value = ["msg1"]
        mock_has_att.return_value = True
        mock_get_pdfs.return_value = [
            {"filename": "inv.pdf", "data": b"x", "mime_type": "application/pdf"}
        ]
        mock_parse.return_value = {
            "parsed": {"vendor_name": "Test", "total_amount": 10.0},
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }
        mock_get_label.return_value = "Label_123"
        mock_persist.return_value = MagicMock()

        process_invoice_messages(self.user)

        mock_get_label.assert_called_once_with(self.user, "UtterIt/InvoiceParsed")
        mock_add_label.assert_called_once_with(self.user, "msg1", "Label_123")
