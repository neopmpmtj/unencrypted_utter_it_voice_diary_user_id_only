"""
Tests for src/invoice_parser/pdf_parser/persistence.py
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from src.invoice_parser.pdf_parser.persistence import persist_invoice_to_db

User = get_user_model()


class PersistInvoiceToDbTestCase(TestCase):
    """Test persist_invoice_to_db function."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="persist_test@example.com",
            password="testpass123",
        )

    @patch("src.invoice_parser.pdf_parser.persistence.route_utterance")
    def test_persist_creates_ingest_item_financial_record_and_hypermarket_lines(
        self, mock_route,
    ):
        mock_route.return_value = None
        parsed = {
            "vendor_name": "Pingo Doce",
            "invoice_number": "INV-001",
            "invoice_date": "2026-03-16",
            "currency": "EUR",
            "line_items": [
                {"description": "Bread", "quantity": 1, "unit_price": 0.49, "total": 0.49},
                {"description": "Milk", "quantity": 2, "unit_price": 1.00, "total": 2.00},
            ],
            "subtotal": 2.49,
            "tax": 0.0,
            "total_amount": 2.49,
        }

        item = persist_invoice_to_db(
            self.user, parsed, "msg123", "invoice.pdf",
        )

        self.assertIsNotNone(item)
        self.assertEqual(item.user, self.user)
        self.assertEqual(item.external_id, "msg123")
        self.assertEqual(item.source_filename, "invoice.pdf")
        self.assertEqual(item.provider, "gmail")
        self.assertEqual(item.item_type, "email")

        from src.ingestion.models import IngestItem
        from src.financial_parser.models import FinancialRecord, FinancialItem, HypermarketLineItem

        self.assertEqual(IngestItem.objects.count(), 1)
        fr = FinancialRecord.objects.get(source_item=item)
        self.assertEqual(fr.record_name, "Pingo Doce")
        self.assertEqual(fr.status, "success")

        items = list(FinancialItem.objects.filter(financial_record=fr).order_by("item_index"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].description, "Bread")
        self.assertEqual(items[0].amount, Decimal("0.49"))
        self.assertEqual(items[1].description, "Milk")
        self.assertEqual(items[1].amount, Decimal("2.00"))

        hyper = list(HypermarketLineItem.objects.filter(financial_record=fr).order_by("line_index"))
        self.assertEqual(len(hyper), 2)
        self.assertEqual(hyper[0].description, "Bread")
        self.assertEqual(hyper[0].gmail_message_id, "msg123")
        self.assertEqual(hyper[0].gmail_filename, "invoice.pdf")

    def test_persist_skips_on_error(self):
        parsed = {"error": "No invoice data found"}
        item = persist_invoice_to_db(self.user, parsed, "msg1", "x.pdf")
        self.assertIsNone(item)

        from src.ingestion.models import IngestItem
        self.assertEqual(IngestItem.objects.count(), 0)

    @patch("src.invoice_parser.pdf_parser.persistence.route_utterance")
    def test_persist_calls_intent_router_with_context_hint(self, mock_route):
        mock_route.return_value = None
        parsed = {
            "vendor_name": "Test",
            "invoice_number": "X",
            "currency": "EUR",
            "line_items": [],
            "total_amount": 0,
        }

        persist_invoice_to_db(self.user, parsed, "m1", "f.pdf")

        mock_route.assert_called_once()
        call_kw = mock_route.call_args[1]
        self.assertIn("context_hint", call_kw)
        self.assertIn("grocery/food invoice", call_kw["context_hint"])
        self.assertIn("finance", call_kw["context_hint"])
        self.assertEqual(call_kw["user"], self.user)

    @patch("src.invoice_parser.pdf_parser.persistence.route_utterance")
    def test_persist_returns_existing_on_integrity_error(self, mock_route):
        mock_route.return_value = None
        parsed = {
            "vendor_name": "Dup",
            "invoice_number": "X",
            "currency": "EUR",
            "line_items": [],
            "total_amount": 1.0,
        }

        first = persist_invoice_to_db(self.user, parsed, "dup_msg_1", "a.pdf")
        self.assertIsNotNone(first)

        second = persist_invoice_to_db(self.user, parsed, "dup_msg_1", "a.pdf")
        self.assertIsNotNone(second)
        self.assertEqual(first.id, second.id)

    @patch("src.invoice_parser.pdf_parser.persistence.route_utterance")
    def test_persist_completes_when_existing_has_no_financial_record(self, mock_route):
        """When IntegrityError and existing IngestItem has no FinancialRecord, complete persistence."""
        mock_route.return_value = None
        from src.ingestion.models import IngestItem, IngestStatus, ItemType, Provider
        from src.financial_parser.models import FinancialRecord, FinancialItem, HypermarketLineItem

        parsed = {
            "vendor_name": "Partial",
            "invoice_number": "P-001",
            "invoice_date": "2026-03-16",
            "currency": "EUR",
            "line_items": [
                {"description": "Item A", "quantity": 1, "unit_price": 2.00, "total": 2.00},
            ],
            "total_amount": 2.00,
        }

        ingest_item = IngestItem.objects.create(
            user=self.user,
            provider=Provider.GMAIL,
            item_type=ItemType.EMAIL,
            external_id="partial_msg_1",
            status=IngestStatus.PROCESSED,
        )
        self.assertEqual(FinancialRecord.objects.filter(source_item=ingest_item).count(), 0)

        result = persist_invoice_to_db(
            self.user, parsed, "partial_msg_1", "partial.pdf",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.id, ingest_item.id)

        fr = FinancialRecord.objects.get(source_item=ingest_item)
        self.assertEqual(fr.record_name, "Partial")
        self.assertEqual(fr.status, "success")
        self.assertEqual(FinancialItem.objects.filter(financial_record=fr).count(), 1)
        self.assertEqual(HypermarketLineItem.objects.filter(financial_record=fr).count(), 1)
