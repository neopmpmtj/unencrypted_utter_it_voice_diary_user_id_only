"""
Unit tests for list_parser services and models.

- parse_list_item: count matching (parsed items == DB rows), FK integrity, list_name
- ListRecord manager: soft-delete visibility (objects vs all_objects)
- delete_list_records_for_item: soft-delete behaviour
- entry delete cascade: soft-delete when source IngestItem is deleted
"""

from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem
from decimal import Decimal

from src.list_parser.models import ListItem, ListRecord, ListRecordStatus
from src.list_parser.services import (
    delete_list_records_for_item,
    format_list_for_display,
    get_list_display_content,
    get_list_item_data,
    parse_formatted_list_text,
    parse_list_item,
    save_list_from_formatted_text,
    soft_delete_list_item_and_descendants,
)


class ParseListItemCountMatchingTests(TestCase):
    """
    Verify that the number of items returned by the LLM matches
    the number of ListItem rows inserted in the database.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listcount@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_parsed_count_matches_db_rows(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Compras", "leite, pão, ovos, manteiga, queijo")
        mock_extract.return_value = (
            "compras",
            "",
            [
                {"text": "leite", "description": "", "due_date": None},
                {"text": "pão", "description": "integral", "due_date": None},
                {"text": "ovos", "description": "", "due_date": None},
                {"text": "manteiga", "description": "", "due_date": None},
                {"text": "queijo", "description": "fresco", "due_date": None},
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        expected_count = 5
        self.assertEqual(result["item_count"], expected_count)
        db_count = ListItem.objects.filter(
            list_record__source_item=self.item
        ).count()
        self.assertEqual(db_count, expected_count)

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_single_item_list(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("", "comprar pão")
        mock_extract.return_value = (
            "itens",
            "",
            [{"text": "comprar pão", "description": "", "due_date": None}],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 1)
        self.assertEqual(
            ListItem.objects.filter(list_record__source_item=self.item).count(), 1
        )

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_items_with_due_dates(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Tarefas", "limpar casa amanhã, lavar roupa sexta")
        mock_extract.return_value = (
            "tarefas",
            "",
            [
                {"text": "limpar casa", "description": "", "due_date": "2026-02-26"},
                {"text": "lavar roupa", "description": "", "due_date": "2026-02-27"},
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 2)
        items = ListItem.objects.filter(
            list_record__source_item=self.item
        ).order_by("item_index")
        self.assertEqual(items[0].due_date, date(2026, 2, 26))
        self.assertEqual(items[1].due_date, date(2026, 2, 27))

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_items_with_quantity(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Compras", "2 leite, 3 pão, manteiga")
        mock_extract.return_value = (
            "compras",
            "",
            [
                {"text": "leite", "description": "", "due_date": None, "quantity": 2},
                {"text": "pão", "description": "", "due_date": None, "quantity": 3},
                {"text": "manteiga", "description": "", "due_date": None, "quantity": None},
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 3)
        items = ListItem.objects.filter(
            list_record__source_item=self.item
        ).order_by("item_index")
        self.assertEqual(items[0].quantity, Decimal("2"))
        self.assertEqual(items[1].quantity, Decimal("3"))
        self.assertIsNone(items[2].quantity)

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_items_with_quantity_and_unit(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Compras", "2 kg farinha, 1.5 litre leite")
        mock_extract.return_value = (
            "compras",
            "",
            [
                {"text": "farinha", "description": "", "due_date": None, "quantity": 2, "unit": "kg"},
                {"text": "leite", "description": "", "due_date": None, "quantity": 1.5, "unit": "litre"},
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 2)
        items = ListItem.objects.filter(
            list_record__source_item=self.item
        ).order_by("item_index")
        self.assertEqual(items[0].quantity, Decimal("2"))
        self.assertEqual(items[0].unit, "kg")
        self.assertEqual(items[1].quantity, Decimal("1.5"))
        self.assertEqual(items[1].unit, "litre")

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_item_parser_outputs_all_metadata_in_single_item(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Compras", "2 kg farinha para segunda")
        mock_extract.return_value = (
            "compras",
            "the weekend",
            [
                {
                    "text": "farinha",
                    "description": "tipo 55",
                    "due_date": "2026-03-02",
                    "quantity": 2,
                    "unit": "kg",
                },
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 1)
        items = ListItem.objects.filter(
            list_record__source_item=self.item
        ).order_by("item_index")
        li = items[0]
        self.assertEqual(get_list_item_data(li, self.user.id)["text"], "farinha")
        self.assertEqual(li.quantity, Decimal("2"))
        self.assertEqual(li.unit, "kg")
        self.assertEqual(li.due_date, date(2026, 3, 2))


class ParseListItemFKIntegrityTests(TestCase):
    """
    Verify that every ListItem has a valid FK chain back
    to the original IngestItem that generated the list.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listfk@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_every_item_links_back_to_source_ingest_item(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Lista", "a, b, c")
        mock_extract.return_value = (
            "itens",
            "",
            [
                {"text": "a", "description": "", "due_date": None},
                {"text": "b", "description": "", "due_date": None},
                {"text": "c", "description": "", "due_date": None},
            ],
            None,
            {},
        )

        parse_list_item(self.item)

        list_record = ListRecord.objects.get(source_item=self.item)
        self.assertEqual(list_record.user, self.user)
        self.assertEqual(list_record.source_item, self.item)

        for li in ListItem.objects.filter(list_record=list_record):
            self.assertEqual(li.list_record.source_item_id, self.item.id)
            self.assertEqual(li.list_record.user_id, self.user.id)


class ListRecordCreationTests(TestCase):
    """Verify ListRecord stores the correct list_name and status."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listrecord@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_list_name_stored_correctly(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Lista de compras", "leite, pão")
        mock_extract.return_value = (
            "compras",
            "",
            [
                {"text": "leite", "description": "", "due_date": None},
                {"text": "pão", "description": "", "due_date": None},
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        record = ListRecord.objects.get(source_item=self.item)
        self.assertEqual(record.list_name, "compras")
        self.assertEqual(record.status, ListRecordStatus.SUCCESS)

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_list_context_stored_correctly(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Guests list for John's birthday party", "Ana, Rui, Paulo")
        mock_extract.return_value = (
            "guests list",
            "John's birthday party",
            [
                {"text": "Ana", "description": "", "due_date": None},
                {"text": "Rui", "description": "", "due_date": None},
                {"text": "Paulo", "description": "", "due_date": None},
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        record = ListRecord.objects.get(source_item=self.item)
        self.assertEqual(record.list_name, "guests list")
        self.assertEqual(record.list_context, "John's birthday party")

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_extraction_failure_marks_failed(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("", "random text")
        mock_extract.return_value = (None, None, None, "No items extracted from text", {})

        result = parse_list_item(self.item)

        self.assertFalse(result["success"])
        record = ListRecord.objects.get(source_item=self.item)
        self.assertEqual(record.status, ListRecordStatus.FAILED)
        self.assertIn("No items", record.error_message)

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_skip_if_already_success(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("Lista", "a, b")
        mock_extract.return_value = (
            "itens",
            "",
            [
                {"text": "a", "description": "", "due_date": None},
                {"text": "b", "description": "", "due_date": None},
            ],
            None,
            {},
        )

        parse_list_item(self.item)
        result2 = parse_list_item(self.item)

        self.assertTrue(result2["success"])
        self.assertTrue(result2.get("skipped"))
        self.assertEqual(ListRecord.objects.filter(source_item=self.item).count(), 1)


class ListRecordManagerTests(TestCase):
    """Test that default manager excludes soft-deleted records; all_objects includes them."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listmgr@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_objects_excludes_soft_deleted(self):
        record = ListRecord.all_objects.create(
            user=self.user,
            source_item=self.item,
            list_name="test",
            status=ListRecordStatus.SUCCESS,
            is_deleted=False,
        )
        self.assertEqual(ListRecord.objects.filter(source_item=self.item).count(), 1)
        self.assertEqual(ListRecord.all_objects.filter(source_item=self.item).count(), 1)

        record.is_deleted = True
        record.deleted_at = timezone.now()
        record.save(update_fields=["is_deleted", "deleted_at"])

        self.assertEqual(ListRecord.objects.filter(source_item=self.item).count(), 0)
        self.assertEqual(ListRecord.all_objects.filter(source_item=self.item).count(), 1)


class DeleteListRecordsForItemTests(TestCase):
    """Tests for delete_list_records_for_item(): soft-delete behaviour."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listdel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_no_records_is_noop(self):
        delete_list_records_for_item(self.item)
        self.assertEqual(ListRecord.all_objects.filter(source_item=self.item).count(), 0)

    def test_soft_deletes_record(self):
        record = ListRecord.all_objects.create(
            user=self.user,
            source_item=self.item,
            list_name="test",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(
            list_record=record,
            parent=None,
            item_index=0,
            text="item a",
        )

        delete_list_records_for_item(self.item)

        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertEqual(ListRecord.objects.filter(source_item=self.item).count(), 0)
        self.assertEqual(ListRecord.all_objects.filter(source_item=self.item).count(), 1)
        self.assertEqual(ListItem.objects.filter(list_record=record).count(), 0)
        self.assertEqual(ListItem.all_objects.filter(list_record=record).count(), 1)
        li = ListItem.all_objects.get(list_record=record)
        self.assertIsNotNone(li.deleted_at)

    def test_already_deleted_records_not_affected(self):
        ListRecord.all_objects.create(
            user=self.user,
            source_item=self.item,
            list_name="test",
            status=ListRecordStatus.FAILED,
            is_deleted=True,
            deleted_at=timezone.now(),
        )

        delete_list_records_for_item(self.item)
        record = ListRecord.all_objects.get(source_item=self.item)
        self.assertTrue(record.is_deleted)


class ListItemSoftDeleteTests(TestCase):
    """Tests for ListItem deleted_at and cascade behaviours."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listitemdel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_list_item_manager_excludes_deleted(self):
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="test",
            status=ListRecordStatus.SUCCESS,
        )
        li = ListItem.objects.create(
            list_record=record, parent=None, item_index=0, text="a"
        )
        self.assertEqual(ListItem.objects.filter(list_record=record).count(), 1)
        self.assertEqual(ListItem.all_objects.filter(list_record=record).count(), 1)

        now = timezone.now()
        li.deleted_at = now
        li.save(update_fields=["deleted_at"])

        self.assertEqual(ListItem.objects.filter(list_record=record).count(), 0)
        self.assertEqual(ListItem.all_objects.filter(list_record=record).count(), 1)
        self.assertEqual(list(record.items.all()), [])

    def test_soft_delete_item_cascades_to_descendants(self):
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="test",
            status=ListRecordStatus.SUCCESS,
        )
        parent = ListItem.objects.create(
            list_record=record, parent=None, item_index=0, text="parent"
        )
        child1 = ListItem.objects.create(
            list_record=record, parent=parent, item_index=0, text="child1"
        )
        child2 = ListItem.objects.create(
            list_record=record, parent=parent, item_index=1, text="child2"
        )
        grandchild = ListItem.objects.create(
            list_record=record, parent=child1, item_index=0, text="grandchild"
        )

        now = timezone.now()
        soft_delete_list_item_and_descendants(parent, now)

        self.assertEqual(ListItem.objects.filter(list_record=record).count(), 0)
        self.assertEqual(ListItem.all_objects.filter(list_record=record).count(), 4)
        for li in ListItem.all_objects.filter(list_record=record):
            self.assertIsNotNone(li.deleted_at)


class FormatListForDisplayTests(TestCase):
    """Tests for format_list_for_display with quantity."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listfmt@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_format_includes_quantity_when_present(self):
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="compras",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(
            list_record=record, item_index=0, text="leite", quantity=Decimal("2")
        )
        ListItem.objects.create(
            list_record=record, item_index=1, text="pão",
        )

        out = format_list_for_display(record)
        self.assertIn("2 x leite", out)
        self.assertIn("- pão", out)
        self.assertNotIn("1 x pão", out)

    def test_format_omits_quantity_when_one_or_null(self):
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="itens",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(
            list_record=record, item_index=0, text="leite", quantity=Decimal("1")
        )

        out = format_list_for_display(record)
        self.assertIn("- leite", out)
        self.assertNotIn("1 x", out)

    def test_format_includes_unit_when_present(self):
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="compras",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(
            list_record=record,
            item_index=0,
            text="farinha",
            quantity=Decimal("2"),
            unit="kg",
        )

        out = format_list_for_display(record)
        self.assertIn("2 kg farinha", out)

    def test_final_output_includes_list_context_when_present(self):
        """Ensure list_context appears in the formatted output when present."""
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="guests list",
            list_context="John's birthday party",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(list_record=record, item_index=0, text="Ana")
        ListItem.objects.create(list_record=record, item_index=1, text="Rui")

        out = format_list_for_display(record)
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "guests list")
        self.assertEqual(lines[1], "John's birthday party")
        self.assertIn("- Ana", out)
        self.assertIn("- Rui", out)

    def test_final_output_omits_list_context_when_null(self):
        """Ensure output is valid when list_context is null/empty."""
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="compras",
            list_context="",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(list_record=record, item_index=0, text="leite")

        out = format_list_for_display(record)
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "compras")
        self.assertIn("- leite", out)
        self.assertEqual(len(lines), 2)

    def test_final_output_includes_quantity_item_unit_per_bulleted_line(self):
        """
        Ensure the final formatted output for an IngestItem list includes at minimum
        list name, list context (when present), and each line has quantity, item text, unit.
        """
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="compras",
            list_context="the weekend",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(
            list_record=record,
            item_index=0,
            text="farinha",
            quantity=Decimal("2"),
            unit="kg",
        )
        ListItem.objects.create(
            list_record=record,
            item_index=1,
            text="leite",
            quantity=Decimal("1.5"),
            unit="litre",
        )
        ListItem.objects.create(
            list_record=record,
            item_index=2,
            text="ovos",
            quantity=Decimal("1"),
            unit="unit",
        )
        ListItem.objects.create(
            list_record=record,
            item_index=3,
            text="pão",
        )

        out = format_list_for_display(record)
        lines = out.strip().split("\n")
        self.assertGreaterEqual(len(lines), 6, "Expected list name + list_context + 4 bulleted lines")
        list_name = lines[0]
        list_context = lines[1]
        bullet_lines = [ln.strip() for ln in lines[2:] if ln.strip().startswith("- ")]

        self.assertEqual(list_name, "compras")
        self.assertEqual(list_context, "the weekend")
        self.assertEqual(len(bullet_lines), 4)

        self.assertIn("2", bullet_lines[0])
        self.assertIn("kg", bullet_lines[0])
        self.assertIn("farinha", bullet_lines[0])

        self.assertIn("1.5", bullet_lines[1])
        self.assertIn("litre", bullet_lines[1])
        self.assertIn("leite", bullet_lines[1])

        self.assertIn("1", bullet_lines[2])
        self.assertIn("unit", bullet_lines[2])
        self.assertIn("ovos", bullet_lines[2])

        self.assertIn("pão", bullet_lines[3])

    def test_format_list_for_display_receives_items_with_full_metadata(self):
        """Assert formatter receives ListItems with quantity, due_date, unit for formatting."""
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="compras",
            list_context="the weekend",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(
            list_record=record,
            item_index=0,
            text="farinha",
            quantity=Decimal("2"),
            unit="kg",
            due_date=date(2026, 3, 2),
        )

        out = format_list_for_display(record)

        self.assertIn("2", out)
        self.assertIn("kg", out)
        self.assertIn("farinha", out)
        items = list(record.items.order_by("item_index"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].quantity, Decimal("2"))
        self.assertEqual(items[0].unit, "kg")
        self.assertEqual(items[0].due_date, date(2026, 3, 2))


class IngestItemListFinalOutputTests(TestCase):
    """
    Ensure the final output for a list-classified IngestItem includes quantity,
    item text, and unit per bulleted line when those fields exist.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listfinal@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    @patch("src.list_parser.list_formatter.services.enhance_list_display", side_effect=lambda x: (x, {}))
    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_pipeline_output_includes_quantity_item_unit_per_line(
        self, mock_decrypt, mock_extract, mock_enhance
    ):
        mock_decrypt.return_value = (
            "Compras",
            "2 kg farinha, 1.5 litre leite, 3 unit ovos, pão",
        )
        mock_extract.return_value = (
            "compras",
            "the weekend",
            [
                {"text": "farinha", "description": "", "due_date": None, "quantity": 2, "unit": "kg"},
                {"text": "leite", "description": "", "due_date": None, "quantity": 1.5, "unit": "litre"},
                {"text": "ovos", "description": "", "due_date": None, "quantity": 3, "unit": "unit"},
                {"text": "pão", "description": "", "due_date": None},
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)
        self.assertTrue(result["success"])

        record = ListRecord.objects.get(source_item=self.item)
        out, _ = get_list_display_content(record)
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "compras")
        self.assertEqual(lines[1], "the weekend")
        bullet_lines = [
            ln.strip()
            for ln in lines[2:]
            if ln.strip().startswith("- ")
        ]

        self.assertIn("2", bullet_lines[0])
        self.assertIn("kg", bullet_lines[0])
        self.assertIn("farinha", bullet_lines[0])

        self.assertIn("1.5", bullet_lines[1])
        self.assertIn("litre", bullet_lines[1])
        self.assertIn("leite", bullet_lines[1])

        self.assertIn("3", bullet_lines[2])
        self.assertIn("unit", bullet_lines[2])
        self.assertIn("ovos", bullet_lines[2])

        self.assertIn("pão", bullet_lines[3])


class EnhanceListDisplayTests(TestCase):
    """Tests for enhance_list_display (list formatter module)."""

    @patch("src.list_parser.list_formatter.services.get_list_formatter_config")
    @patch("src.list_parser.list_formatter.services.OpenAI")
    def test_returns_raw_text_when_api_fails(self, mock_openai_cls, mock_config):
        from src.list_parser.list_formatter.services import enhance_list_display

        mock_cfg = MagicMock()
        mock_cfg.enabled = True
        mock_cfg.openai_api_key = "test-key"
        mock_config.return_value = mock_cfg

        mock_openai_cls.return_value.chat.completions.create.side_effect = Exception("API error")
        raw = "compras\n- leite\n- pão"
        result, _ = enhance_list_display(raw)
        self.assertEqual(result, raw)

    @patch("src.list_parser.list_formatter.services.get_list_formatter_config")
    @patch("src.list_parser.list_formatter.services.OpenAI")
    def test_returns_enhanced_text_when_api_succeeds(self, mock_openai_cls, mock_config):
        from src.list_parser.list_formatter.services import enhance_list_display

        mock_cfg = MagicMock()
        mock_cfg.enabled = True
        mock_cfg.openai_api_key = "test-key"
        mock_cfg.model = "gpt-4.1-mini"
        mock_cfg.temperature = 0.2
        mock_cfg.max_output_tokens = 2048
        mock_cfg.get_prompt.side_effect = lambda x: f"prompt:{x}"
        mock_config.return_value = mock_cfg

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="compras\n\n- leite\n- pão"))]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        mock_openai_cls.return_value.chat.completions.create.return_value = mock_response
        raw = "compras\n- leite\n- pão"
        result, _ = enhance_list_display(raw)
        self.assertEqual(result, "compras\n\n- leite\n- pão")

    def test_returns_raw_text_when_disabled(self):
        from src.list_parser.list_formatter.services import enhance_list_display

        raw = "compras\n- leite"
        with patch(
            "src.list_parser.list_formatter.services.get_list_formatter_config"
        ) as mock_config:
            mock_cfg = MagicMock()
            mock_cfg.enabled = False
            mock_config.return_value = mock_cfg
            result, _ = enhance_list_display(raw)
        self.assertEqual(result, raw)

    def test_returns_raw_text_when_empty(self):
        from src.list_parser.list_formatter.services import enhance_list_display

        result, _ = enhance_list_display("")
        self.assertEqual(result, "")
        result, _ = enhance_list_display("   ")
        self.assertEqual(result, "   ")


class ParseFormattedListTextTests(TestCase):
    """Tests for parse_formatted_list_text with quantity."""

    def test_parses_quantity_x_format(self):
        text = "compras\n- 2 x leite\n- pão\n- 1.5 x farinha"
        name, list_context, items = parse_formatted_list_text(text)
        self.assertEqual(name, "compras")
        self.assertEqual(list_context, "")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0], {"text": "leite", "quantity": Decimal("2")})
        self.assertEqual(items[1], {"text": "pão"})
        self.assertEqual(items[2], {"text": "farinha", "quantity": Decimal("1.5")})

    def test_parses_quantity_paren_format(self):
        text = "lista\n- leite (2)\n- pão"
        name, list_context, items = parse_formatted_list_text(text)
        self.assertEqual(list_context, "")
        self.assertEqual(items[0], {"text": "leite", "quantity": Decimal("2")})
        self.assertEqual(items[1], {"text": "pão"})

    def test_parses_without_quantity(self):
        text = "itens\n- a\n- b"
        name, list_context, items = parse_formatted_list_text(text)
        self.assertEqual(list_context, "")
        self.assertEqual(items[0], {"text": "a"})
        self.assertEqual(items[1], {"text": "b"})

    def test_parses_quantity_and_unit_format(self):
        text = "compras\n- 2 kg farinha\n- 1.5 litre leite\n- pão"
        name, list_context, items = parse_formatted_list_text(text)
        self.assertEqual(name, "compras")
        self.assertEqual(list_context, "")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0], {"text": "farinha", "quantity": Decimal("2"), "unit": "kg"})
        self.assertEqual(items[1], {"text": "leite", "quantity": Decimal("1.5"), "unit": "litre"})
        self.assertEqual(items[2], {"text": "pão"})

    def test_parses_list_context(self):
        text = "guests list\nJohn's birthday party\n- Ana\n- Rui"
        name, list_context, items = parse_formatted_list_text(text)
        self.assertEqual(name, "guests list")
        self.assertEqual(list_context, "John's birthday party")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0], {"text": "Ana"})
        self.assertEqual(items[1], {"text": "Rui"})


class SaveListFromFormattedTextQuantityTests(TestCase):
    """Tests for save_list_from_formatted_text with quantity."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listsave@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_saves_quantity_from_formatted_text(self):
        text = "compras\n- 2 x leite\n- pão"
        record = save_list_from_formatted_text(self.item, text)
        self.assertIsNotNone(record)
        items = ListItem.objects.filter(list_record=record).order_by("item_index")
        self.assertEqual(items[0].quantity, Decimal("2"))
        self.assertEqual(get_list_item_data(items[0], self.user.id)["text"], "leite")
        self.assertIsNone(items[1].quantity)
        self.assertEqual(get_list_item_data(items[1], self.user.id)["text"], "pão")

    def test_saves_quantity_and_unit_from_formatted_text(self):
        text = "compras\n- 2 kg farinha\n- pão"
        record = save_list_from_formatted_text(self.item, text)
        self.assertIsNotNone(record)
        items = ListItem.objects.filter(list_record=record).order_by("item_index")
        self.assertEqual(items[0].quantity, Decimal("2"))
        self.assertEqual(items[0].unit, "kg")
        self.assertEqual(get_list_item_data(items[0], self.user.id)["text"], "farinha")
        self.assertEqual(get_list_item_data(items[1], self.user.id)["text"], "pão")

    def test_saves_list_context_from_formatted_text(self):
        text = "guests list\nJohn's birthday party\n- Ana\n- Rui"
        record = save_list_from_formatted_text(self.item, text)
        self.assertIsNotNone(record)
        self.assertEqual(record.list_name, "guests list")
        self.assertEqual(record.list_context, "John's birthday party")
        items = ListItem.objects.filter(list_record=record).order_by("item_index")
        self.assertEqual(get_list_item_data(items[0], self.user.id)["text"], "Ana")
        self.assertEqual(get_list_item_data(items[1], self.user.id)["text"], "Rui")


class HierarchicalListTests(TestCase):
    """Tests for hierarchical sublist support."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listhier@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_parse_list_item_hierarchical(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = (
            "Christmas shopping",
            "Paul - book, shirt; John - mug",
        )
        mock_extract.return_value = (
            "Christmas shopping",
            "Christmas",
            [
                {
                    "text": "Paul",
                    "description": "",
                    "due_date": None,
                    "quantity": None,
                    "unit": None,
                    "children": [
                        {"text": "book", "description": "", "due_date": None, "quantity": None, "unit": None},
                        {"text": "shirt", "description": "", "due_date": None, "quantity": None, "unit": None},
                    ],
                },
                {
                    "text": "John",
                    "description": "",
                    "due_date": None,
                    "quantity": None,
                    "unit": None,
                    "children": [
                        {"text": "mug", "description": "", "due_date": None, "quantity": None, "unit": None},
                    ],
                },
            ],
            None,
            {},
        )

        result = parse_list_item(self.item)

        self.assertTrue(result["success"])
        self.assertEqual(result["item_count"], 5)
        record = ListRecord.objects.get(source_item=self.item)
        user_id = self.item.user_id
        top_level = list(record.items.filter(parent=None).order_by("item_index"))
        self.assertEqual(len(top_level), 2)
        self.assertEqual(get_list_item_data(top_level[0], user_id)["text"], "Paul")
        self.assertEqual(get_list_item_data(top_level[1], user_id)["text"], "John")
        paul_children = list(record.items.filter(parent=top_level[0]).order_by("item_index"))
        self.assertEqual(len(paul_children), 2)
        self.assertEqual(get_list_item_data(paul_children[0], user_id)["text"], "book")
        self.assertEqual(get_list_item_data(paul_children[1], user_id)["text"], "shirt")
        john_children = list(record.items.filter(parent=top_level[1]).order_by("item_index"))
        self.assertEqual(len(john_children), 1)
        self.assertEqual(get_list_item_data(john_children[0], user_id)["text"], "mug")

    @patch("src.list_parser.services.extract_list_items")
    @patch("src.common.utils.content.get_item_title_and_content")
    def test_fk_chain_child_to_source(self, mock_decrypt, mock_extract):
        mock_decrypt.return_value = ("List", "Paul - a")
        mock_extract.return_value = (
            "list",
            "",
            [{"text": "Paul", "children": [{"text": "a", "description": "", "due_date": None, "quantity": None, "unit": None}]}],
            None,
            {},
        )

        parse_list_item(self.item)

        record = ListRecord.objects.get(source_item=self.item)
        parent_li = record.items.get(parent=None)
        child_li = record.items.get(parent=parent_li)
        self.assertEqual(child_li.list_record, record)
        self.assertEqual(child_li.parent, parent_li)
        self.assertEqual(record.source_item, self.item)

    def test_format_list_for_display_hierarchical(self):
        record = ListRecord.objects.create(
            user=self.user,
            source_item=self.item,
            list_name="Christmas shopping",
            list_context="Christmas",
            status=ListRecordStatus.SUCCESS,
        )
        paul = ListItem.objects.create(
            list_record=record, parent=None, item_index=0, text="Paul",
        )
        ListItem.objects.create(
            list_record=record, parent=paul, item_index=0, text="book",
        )
        ListItem.objects.create(
            list_record=record, parent=paul, item_index=1, text="shirt",
        )
        john = ListItem.objects.create(
            list_record=record, parent=None, item_index=1, text="John",
        )
        ListItem.objects.create(
            list_record=record, parent=john, item_index=0, text="mug",
        )

        out = format_list_for_display(record)
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "Christmas shopping")
        self.assertEqual(lines[1], "Christmas")
        self.assertIn("- Paul", out)
        self.assertIn("  - book", out)
        self.assertIn("  - shirt", out)
        self.assertIn("- John", out)
        self.assertIn("  - mug", out)

    def test_parse_formatted_list_text_hierarchical(self):
        text = "Christmas shopping\nChristmas\n- Paul\n  - book\n  - shirt\n- John\n  - mug"
        name, list_context, items = parse_formatted_list_text(text)
        self.assertEqual(name, "Christmas shopping")
        self.assertEqual(list_context, "Christmas")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["text"], "Paul")
        self.assertEqual(items[0]["children"][0]["text"], "book")
        self.assertEqual(items[0]["children"][1]["text"], "shirt")
        self.assertEqual(items[1]["text"], "John")
        self.assertEqual(items[1]["children"][0]["text"], "mug")

    def test_save_and_roundtrip_hierarchical(self):
        text = "Christmas shopping\nChristmas\n- Paul\n  - book\n  - shirt\n- John\n  - mug"
        record = save_list_from_formatted_text(self.item, text)
        self.assertIsNotNone(record)
        out = format_list_for_display(record)
        self.assertIn("Christmas shopping", out)
        self.assertIn("- Paul", out)
        self.assertIn("  - book", out)
        self.assertIn("  - shirt", out)
        self.assertIn("- John", out)
        self.assertIn("  - mug", out)


class EntryDeleteCascadeTests(TestCase):
    """
    Test that soft-deleting an IngestItem also soft-deletes the ListRecord.
    Mirrors CalendarEvent cascade tests in entries/tests/test_views.py.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="listcascade@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
        )

    def test_delete_list_records_soft_deletes_on_entry_delete(self):
        record = ListRecord.all_objects.create(
            user=self.user,
            source_item=self.item,
            list_name="compras",
            status=ListRecordStatus.SUCCESS,
        )
        ListItem.objects.create(list_record=record, item_index=0, text="leite")
        ListItem.objects.create(list_record=record, item_index=1, text="pão")

        delete_list_records_for_item(self.item)

        self.assertEqual(ListRecord.objects.filter(source_item=self.item).count(), 0)
        self.assertEqual(ListRecord.all_objects.filter(source_item=self.item).count(), 1)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertEqual(ListItem.objects.filter(list_record=record).count(), 0)
        self.assertEqual(ListItem.all_objects.filter(list_record=record).count(), 2)
        for li in ListItem.all_objects.filter(list_record=record):
            self.assertIsNotNone(li.deleted_at)
