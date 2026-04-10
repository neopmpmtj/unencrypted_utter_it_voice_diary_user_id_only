"""
Tests for src/invoice_parser/pdf_parser/views.py
"""

from unittest.mock import patch

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

from src.invoice_parser.pdf_parser.views import parse_pdf_api

User = get_user_model()


class ParsePdfApiTestCase(TestCase):
    """Test parse_pdf_api view."""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            password="testpass123",
        )
        self.admin_user.is_app_admin = True
        self.admin_user.save()

        self.normal_user = User.objects.create_user(
            email="normal@example.com",
            password="testpass123",
        )
        self.normal_user.save()

    def test_get_not_allowed(self):
        request = self.factory.get("/invoice-parser/api/parse-pdf/")
        request.user = self.admin_user
        response = parse_pdf_api(request)
        self.assertEqual(response.status_code, 405)

    def test_non_admin_forbidden(self):
        request = self.factory.post("/invoice-parser/api/parse-pdf/")
        request.user = self.normal_user
        response = parse_pdf_api(request)
        self.assertEqual(response.status_code, 403)

    @patch("src.invoice_parser.pdf_parser.views.verify_gmail_permissions")
    def test_no_gmail_returns_403(self, mock_verify):
        mock_verify.return_value = False
        request = self.factory.post("/invoice-parser/api/parse-pdf/")
        request.user = self.admin_user
        response = parse_pdf_api(request)
        self.assertEqual(response.status_code, 403)

    @patch("src.invoice_parser.pdf_parser.views.process_invoice_messages")
    @patch("src.invoice_parser.pdf_parser.views.verify_gmail_permissions")
    def test_success_returns_json(self, mock_verify, mock_process):
        mock_verify.return_value = True
        mock_process.return_value = {
            "results": [],
            "errors": [],
            "summary": {"messages_found": 0, "pdfs_parsed": 0},
        }
        request = self.factory.post("/invoice-parser/api/parse-pdf/")
        request.user = self.admin_user
        response = parse_pdf_api(request)
        self.assertEqual(response.status_code, 200)
        import json
        data = json.loads(response.content)
        self.assertIn("results", data)
        self.assertIn("summary", data)

    @patch("src.invoice_parser.pdf_parser.views.process_invoice_messages")
    @patch("src.invoice_parser.pdf_parser.views.verify_gmail_permissions")
    def test_service_exception_returns_503(self, mock_verify, mock_process):
        mock_verify.return_value = True
        mock_process.side_effect = RuntimeError("Unexpected error")
        request = self.factory.post("/invoice-parser/api/parse-pdf/")
        request.user = self.admin_user
        response = parse_pdf_api(request)
        self.assertEqual(response.status_code, 503)
