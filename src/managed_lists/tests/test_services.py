"""
Tests for managed_lists services.
"""

from datetime import date, time
from unittest.mock import MagicMock, patch

from django.test import TestCase

from src.managed_lists.services import (
    _parse_due_date,
    _parse_due_time,
    _parse_priority,
    _strip_markdown_json_fences,
    _to_json_safe,
    _validate_and_normalize_item,
)


class StripMarkdownJsonFencesTest(TestCase):
    def test_no_fences(self):
        self.assertEqual(_strip_markdown_json_fences('{"key": "value"}'), '{"key": "value"}')

    def test_json_fences(self):
        text = '```json\n{"key": "value"}\n```'
        self.assertEqual(_strip_markdown_json_fences(text), '{"key": "value"}')

    def test_plain_fences(self):
        text = '```\n{"key": "value"}\n```'
        self.assertEqual(_strip_markdown_json_fences(text), '{"key": "value"}')


class ParseDueDateTest(TestCase):
    def test_valid_date(self):
        self.assertEqual(_parse_due_date("2026-03-15"), date(2026, 3, 15))

    def test_none(self):
        self.assertIsNone(_parse_due_date(None))

    def test_invalid(self):
        self.assertIsNone(_parse_due_date("not-a-date"))

    def test_empty_string(self):
        self.assertIsNone(_parse_due_date(""))


class ParseDueTimeTest(TestCase):
    def test_valid_time(self):
        self.assertEqual(_parse_due_time("14:30"), time(14, 30))

    def test_none(self):
        self.assertIsNone(_parse_due_time(None))

    def test_invalid(self):
        self.assertIsNone(_parse_due_time("not-a-time"))


class ParsePriorityTest(TestCase):
    def test_valid_priority(self):
        self.assertEqual(_parse_priority(5), 5)
        self.assertEqual(_parse_priority(1), 1)

    def test_none_returns_medium(self):
        self.assertEqual(_parse_priority(None), 3)

    def test_out_of_range(self):
        self.assertEqual(_parse_priority(0), 3)
        self.assertEqual(_parse_priority(6), 3)

    def test_string_number(self):
        self.assertEqual(_parse_priority("4"), 4)


class ToJsonSafeTest(TestCase):
    def test_decimal(self):
        from decimal import Decimal
        result = _to_json_safe({"amount": Decimal("10.5")})
        self.assertEqual(result, {"amount": 10.5})

    def test_nested(self):
        from decimal import Decimal
        result = _to_json_safe([{"val": Decimal("1")}])
        self.assertEqual(result, [{"val": 1.0}])


class ValidateAndNormalizeItemTest(TestCase):
    def test_valid_item(self):
        item = {
            "text": "Call dentist",
            "description": "Annual checkup",
            "priority": 4,
            "due_date": "2026-03-15",
            "topic": "health",
            "subtopic": "dentist",
            "entity_name": "Dr. Silva",
            "entity_type": "person",
        }
        result = _validate_and_normalize_item(item, 0)
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "Call dentist")
        self.assertEqual(result["priority"], 4)
        self.assertEqual(result["entity_type"], "person")

    def test_missing_text(self):
        result = _validate_and_normalize_item({"description": "no text"}, 0)
        self.assertIsNone(result)

    def test_not_a_dict(self):
        result = _validate_and_normalize_item("string", 0)
        self.assertIsNone(result)

    def test_invalid_entity_type(self):
        item = {"text": "task", "entity_type": "invalid_type"}
        result = _validate_and_normalize_item(item, 0)
        self.assertIsNotNone(result)
        self.assertEqual(result["entity_type"], "")

    def test_invalid_recurrence(self):
        item = {"text": "task", "recurrence_rule": "biweekly"}
        result = _validate_and_normalize_item(item, 0)
        self.assertIsNotNone(result)
        self.assertEqual(result["recurrence_rule"], "")

    def test_children(self):
        item = {
            "text": "Parent task",
            "children": [
                {"text": "Subtask 1"},
                {"text": "Subtask 2"},
            ],
        }
        result = _validate_and_normalize_item(item, 0)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["children"]), 2)
