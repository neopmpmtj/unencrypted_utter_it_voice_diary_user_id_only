"""Tests for management command ingest_text."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.contrib.auth import get_user_model

from src.ingestion.models import IngestItem

User = get_user_model()


class IngestTextCommandTests(TestCase):
    @patch("src.classification.tasks.classify_item_task")
    def test_creates_item_same_as_web_path(self, mock_classify):
        user = User.objects.create_user(email="cmd@example.com", password="secret")
        user.is_test_user = True
        user.save()

        out = StringIO()
        call_command(
            "ingest_text",
            email="cmd@example.com",
            text="CLI diary line",
            stdout=out,
        )

        item = IngestItem.objects.get(user=user)
        self.assertEqual(item.item_type, "text")
        self.assertEqual(item.content_text, "CLI diary line")
        self.assertIn(str(item.id), out.getvalue())
        mock_classify.delay.assert_called_once()

    def test_requires_user_selector(self):
        with self.assertRaises(CommandError):
            call_command("ingest_text", text="x")
