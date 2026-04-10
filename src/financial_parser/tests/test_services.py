"""
Unit tests for financial_parser services and models.

- parse_financial_item: count matching, FK integrity, record_name
- FinancialRecord manager: soft-delete visibility (objects vs all_objects)
- delete_financial_records_for_item: soft-delete behaviour
- format_financial_for_display, parse_formatted_financial_text, save_financial_from_formatted_text
- enhance_financial_display: fallback on API error
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem

from src.financial_parser.models import (
    FinancialItem,
    FinancialRecord,
    FinancialRecordStatus,
)
from src.financial_parser.services import (
    delete_financial_records_for_item,
    format_financial_for_display,
    get_financial_display_content,
    parse_financial_item,
    parse_formatted_financial_text,
    save_financial_from_formatted_text,
)


class ParseFinancialItemTests(TestCase):
    """Verify parse_financial_item creates correct FinancialRecord and FinancialItem rows."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="fin@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    @patch("src.financial_parser.services.extract_financial_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_parsed_count_matches_db_rows(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("", "gastei 20 no café, 12 no almoço, 8 no uber")
        mock_extract.return_value = (
            "Despesas de hoje",
            "",
            [
                {"type": "expense", "amount": 20, "currency": "EUR", "category": "Food", "description": "café"},
                {"type": "expense", "amount": 12, "currency": "EUR", "category": "Food", "description": "almoço"},
                {"type": "expense", "amount": 8, "currency": "EUR", "category": "Transport", "description": "uber"},
            ],
            None,
            {},
        )

        result = parse_financial_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 3)
        db_count = FinancialItem.objects.filter(
            financial_record__source_item=self.item
        ).count()
        self.assertEqual(db_count, 3)

    @patch("src.financial_parser.services.extract_financial_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_expense_and_income(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("", "recebi 500, paguei 50 ao dentista")
        mock_extract.return_value = (
            "Misto",
            "",
            [
                {"type": "income", "amount": 500, "currency": "EUR", "category": "Freelance", "description": "projeto"},
                {"type": "expense", "amount": 50, "currency": "EUR", "category": "Health", "description": "dentista"},
            ],
            None,
            {},
        )

        result = parse_financial_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 2)
        items = FinancialItem.objects.filter(
            financial_record__source_item=self.item
        ).order_by("item_index")
        self.assertEqual(items[0].type, "income")
        self.assertEqual(items[0].amount, Decimal("500"))
        self.assertEqual(items[1].type, "expense")
        self.assertEqual(items[1].amount, Decimal("50"))

    @patch("src.financial_parser.services.extract_financial_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_extraction_error_marks_failed(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("", "nada financeiro aqui")
        mock_extract.return_value = (None, None, None, "No financial information to extract", {})

        result = parse_financial_item(self.item)

        self.assertFalse(result["success"])
        self.assertIn("No financial information", result["error"])
        record = FinancialRecord.objects.get(source_item=self.item)
        self.assertEqual(record.status, FinancialRecordStatus.FAILED)

    @patch("src.financial_parser.services.extract_financial_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_empty_content_returns_error(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("", "")

        result = parse_financial_item(self.item)

        self.assertFalse(result["success"])
        self.assertIn("no content", result["error"].lower())

    @patch("src.financial_parser.services.extract_financial_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_skips_when_record_exists(self, mock_decrypt, mock_extract):
        record = FinancialRecord.objects.create(
            user=self.user,
            source_item=self.item,
            record_name="existing",
            status=FinancialRecordStatus.SUCCESS,
        )
        FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("10"),
            currency="EUR",
        )

        result = parse_financial_item(self.item)

        self.assertTrue(result["success"])
        self.assertTrue(result.get("skipped"))
        mock_extract.assert_not_called()

    @patch("src.financial_parser.services.extract_financial_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_financial_parser_outputs_all_metadata_in_single_item(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Despesas", "café 3.50 no Starbucks, cartão")
        mock_extract.return_value = (
            "Despesas de hoje",
            "viagem",
            [
                {
                    "type": "expense",
                    "amount": 3.50,
                    "currency": "EUR",
                    "category": "Food",
                    "merchant": "Starbucks",
                    "transaction_date": "2026-02-28",
                    "description": "café",
                    "payment_method": "card",
                },
            ],
            None,
            {},
        )

        result = parse_financial_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 1)
        items = FinancialItem.objects.filter(
            financial_record__source_item=self.item
        ).order_by("item_index")
        fi = items[0]
        self.assertEqual(fi.type, "expense")
        self.assertEqual(fi.amount, Decimal("3.50"))
        self.assertEqual(fi.currency, "EUR")
        self.assertEqual(fi.category, "Food")
        self.assertEqual(fi.merchant, "Starbucks")
        self.assertEqual(fi.transaction_date, date(2026, 2, 28))
        self.assertEqual(fi.description, "café")
        self.assertEqual(fi.payment_method, "card")


class FinancialRecordManagerTests(TestCase):
    """Verify FinancialRecord manager excludes soft-deleted records."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="finmgr@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_objects_excludes_soft_deleted(self):
        record = FinancialRecord.all_objects.create(
            user=self.user,
            source_item=self.item,
            record_name="test",
            status=FinancialRecordStatus.SUCCESS,
            is_deleted=False,
        )
        self.assertEqual(FinancialRecord.objects.filter(source_item=self.item).count(), 1)
        self.assertEqual(FinancialRecord.all_objects.filter(source_item=self.item).count(), 1)

        record.is_deleted = True
        record.deleted_at = timezone.now()
        record.save(update_fields=["is_deleted", "deleted_at"])

        self.assertEqual(FinancialRecord.objects.filter(source_item=self.item).count(), 0)
        self.assertEqual(FinancialRecord.all_objects.filter(source_item=self.item).count(), 1)


class DeleteFinancialRecordsForItemTests(TestCase):
    """Tests for delete_financial_records_for_item: soft-delete behaviour."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="findel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_no_records_is_noop(self):
        delete_financial_records_for_item(self.item)
        self.assertEqual(FinancialRecord.all_objects.filter(source_item=self.item).count(), 0)

    def test_soft_deletes_record(self):
        record = FinancialRecord.all_objects.create(
            user=self.user,
            source_item=self.item,
            record_name="test",
            status=FinancialRecordStatus.SUCCESS,
        )
        FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("10"),
            currency="EUR",
        )

        delete_financial_records_for_item(self.item)

        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertEqual(FinancialRecord.objects.filter(source_item=self.item).count(), 0)
        self.assertEqual(FinancialRecord.all_objects.filter(source_item=self.item).count(), 1)
        self.assertEqual(FinancialItem.objects.filter(financial_record=record).count(), 0)
        self.assertEqual(FinancialItem.all_objects.filter(financial_record=record).count(), 1)
        fi = FinancialItem.all_objects.get(financial_record=record)
        self.assertIsNotNone(fi.deleted_at)


class FinancialItemSoftDeleteTests(TestCase):
    """Tests for FinancialItem deleted_at and cascade behaviours."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="finitemdel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_financial_item_manager_excludes_deleted(self):
        record = FinancialRecord.objects.create(
            user=self.user,
            source_item=self.item,
            record_name="test",
            status=FinancialRecordStatus.SUCCESS,
        )
        fi = FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("10"),
            currency="EUR",
        )
        self.assertEqual(FinancialItem.objects.filter(financial_record=record).count(), 1)
        self.assertEqual(FinancialItem.all_objects.filter(financial_record=record).count(), 1)

        now = timezone.now()
        fi.deleted_at = now
        fi.save(update_fields=["deleted_at"])

        self.assertEqual(FinancialItem.objects.filter(financial_record=record).count(), 0)
        self.assertEqual(FinancialItem.all_objects.filter(financial_record=record).count(), 1)
        self.assertEqual(list(record.items.all()), [])


class FormatFinancialForDisplayTests(TestCase):
    """Tests for format_financial_for_display."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="finfmt@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_format_includes_amount_and_currency(self):
        record = FinancialRecord.objects.create(
            user=self.user,
            source_item=self.item,
            record_name="Despesas",
            status=FinancialRecordStatus.SUCCESS,
        )
        FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("20.50"),
            currency="EUR",
            description="café",
        )

        out = format_financial_for_display(record)
        self.assertIn("Despesas", out)
        self.assertIn("café", out)
        self.assertIn("20.50", out)
        self.assertIn("EUR", out)

    def test_format_includes_record_context(self):
        record = FinancialRecord.objects.create(
            user=self.user,
            source_item=self.item,
            record_name="Despesas",
            record_context="viagem a Paris",
            status=FinancialRecordStatus.SUCCESS,
        )
        FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("15"),
            currency="EUR",
            description="almoço",
        )

        out = format_financial_for_display(record)
        self.assertIn("viagem a Paris", out)

    def test_format_financial_for_display_receives_items_with_full_metadata(self):
        """Assert formatter receives FinancialItems with full metadata for formatting."""
        record = FinancialRecord.objects.create(
            user=self.user,
            source_item=self.item,
            record_name="Despesas",
            record_context="viagem",
            status=FinancialRecordStatus.SUCCESS,
        )
        FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("25.50"),
            currency="EUR",
            category="Food",
            merchant="Restaurante X",
            transaction_date=date(2026, 2, 28),
            description="",
            payment_method="card",
        )

        out = format_financial_for_display(record)

        self.assertIn("25.50", out)
        self.assertIn("EUR", out)
        self.assertIn("Restaurante X", out)
        items = list(record.items.order_by("item_index"))
        self.assertEqual(len(items), 1)
        fi = items[0]
        self.assertEqual(fi.amount, Decimal("25.50"))
        self.assertEqual(fi.currency, "EUR")
        self.assertEqual(fi.merchant, "Restaurante X")
        self.assertEqual(fi.transaction_date, date(2026, 2, 28))
        self.assertEqual(fi.payment_method, "card")


class GetFinancialDisplayContentTests(TestCase):
    """Tests for get_financial_display_content (format + enhance)."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="findisp@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    @patch("src.financial_parser.financial_formatter.services.enhance_financial_display")
    def test_returns_formatted_content(self, mock_enhance):
        record = FinancialRecord.objects.create(
            user=self.user,
            source_item=self.item,
            record_name="Despesas",
            status=FinancialRecordStatus.SUCCESS,
        )
        FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("10"),
            currency="EUR",
            description="café",
        )
        mock_enhance.side_effect = lambda rec: (format_financial_for_display(rec), {})

        out, _ = get_financial_display_content(record)
        self.assertIn("Despesas", out)
        self.assertIn("café", out)
        self.assertIn("10", out)


class ParseFormattedFinancialTextTests(TestCase):
    """Tests for parse_formatted_financial_text."""

    def test_parses_colon_format(self):
        text = "Despesas de hoje\n- Café: 3 EUR\n- Almoço: 12 EUR"
        name, ctx, items = parse_formatted_financial_text(text)
        self.assertEqual(name, "Despesas de hoje")
        self.assertEqual(ctx, "")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["description"], "Café")
        self.assertEqual(items[0]["amount"], Decimal("3"))
        self.assertEqual(items[0]["currency"], "EUR")
        self.assertEqual(items[1]["description"], "Almoço")
        self.assertEqual(items[1]["amount"], Decimal("12"))

    def test_parses_with_record_context(self):
        text = "Despesas\nviagem a Paris\n- Jantar: 45 EUR"
        name, ctx, items = parse_formatted_financial_text(text)
        self.assertEqual(name, "Despesas")
        self.assertEqual(ctx, "viagem a Paris")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["amount"], Decimal("45"))

    def test_empty_returns_defaults(self):
        name, ctx, items = parse_formatted_financial_text("")
        self.assertEqual(name, "Despesas")
        self.assertEqual(ctx, "")
        self.assertEqual(items, [])


class SaveFinancialFromFormattedTextTests(TestCase):
    """Tests for save_financial_from_formatted_text."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="finsave@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_saves_items_to_db(self):
        text = "Despesas\n- Café: 3 EUR\n- Almoço: 12 EUR"
        record = save_financial_from_formatted_text(self.item, text)
        self.assertIsNotNone(record)
        self.assertEqual(record.record_name, "Despesas")
        self.assertEqual(FinancialItem.objects.filter(financial_record=record).count(), 2)

    def test_returns_none_when_no_items(self):
        record = save_financial_from_formatted_text(self.item, "Despesas\n")
        self.assertIsNone(record)

    def test_replaces_existing_record(self):
        save_financial_from_formatted_text(self.item, "Despesas\n- A: 1 EUR")
        record2 = save_financial_from_formatted_text(self.item, "Outras\n- B: 2 EUR")
        self.assertIsNotNone(record2)
        self.assertEqual(record2.record_name, "Outras")
        self.assertEqual(FinancialRecord.all_objects.filter(source_item=self.item).count(), 2)
        deleted = FinancialRecord.all_objects.filter(source_item=self.item, is_deleted=True)
        self.assertEqual(deleted.count(), 1)


class EnhanceFinancialDisplayTests(TestCase):
    """Tests for enhance_financial_display (financial formatter module)."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="enhance@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def _create_record_with_item(self):
        record = FinancialRecord.objects.create(
            user=self.user,
            source_item=self.item,
            record_name="Despesas",
            status=FinancialRecordStatus.SUCCESS,
        )
        FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("3"),
            currency="EUR",
            description="Café",
        )
        return record

    @patch("src.financial_parser.financial_formatter.services.get_financial_formatter_config")
    @patch("src.financial_parser.financial_formatter.services.OpenAI")
    def test_returns_raw_text_when_api_fails(self, mock_openai_cls, mock_config):
        from src.financial_parser.financial_formatter.services import enhance_financial_display

        mock_cfg = MagicMock()
        mock_cfg.enabled = True
        mock_cfg.openai_api_key = "test-key"
        mock_config.return_value = mock_cfg

        mock_openai_cls.return_value.chat.completions.create.side_effect = Exception("API error")
        record = self._create_record_with_item()
        result, _ = enhance_financial_display(record)
        expected = format_financial_for_display(record)
        self.assertEqual(result, expected)

    @patch("src.financial_parser.financial_formatter.services.get_financial_formatter_config")
    @patch("src.financial_parser.financial_formatter.services.OpenAI")
    def test_returns_enhanced_text_when_api_succeeds(self, mock_openai_cls, mock_config):
        from src.financial_parser.financial_formatter.services import enhance_financial_display

        mock_cfg = MagicMock()
        mock_cfg.enabled = True
        mock_cfg.openai_api_key = "test-key"
        mock_cfg.model = "gpt-4.1-mini"
        mock_cfg.temperature = 0.2
        mock_cfg.max_output_tokens = 4096
        mock_cfg.get_prompt_from_record = MagicMock(return_value="prompt")
        mock_config.return_value = mock_cfg

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Despesas\n\n- Café: 3 EUR"))]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        mock_openai_cls.return_value.chat.completions.create.return_value = mock_response
        record = self._create_record_with_item()
        result, _ = enhance_financial_display(record)
        self.assertEqual(result, "Despesas\n\n- Café: 3 EUR")

    def test_returns_raw_text_when_disabled(self):
        from src.financial_parser.financial_formatter.services import enhance_financial_display

        record = self._create_record_with_item()
        with patch(
            "src.financial_parser.financial_formatter.services.get_financial_formatter_config"
        ) as mock_config:
            mock_cfg = MagicMock()
            mock_cfg.enabled = False
            mock_config.return_value = mock_cfg
            result, _ = enhance_financial_display(record)
        expected = format_financial_for_display(record)
        self.assertEqual(result, expected)
