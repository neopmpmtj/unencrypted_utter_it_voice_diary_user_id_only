"""Tests for management command ingest_media."""

import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.contrib.auth import get_user_model

from src.ingestion.models import IngestItem, ItemFile, FileRole

User = get_user_model()


class IngestMediaCommandTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = User.objects.create_user(
            email="media@example.com", password="secret"
        )
        cls.user.is_test_user = True
        cls.user.save()

    def _make_temp_file(self, suffix: str = ".jpg", content: bytes = b"fake-image-data") -> Path:
        """Create a temporary file and return its path. Caller must clean up."""
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    @patch("src.classification.tasks.classify_item_task")
    def test_creates_entry_with_single_file(self, mock_classify):
        tmp_path = self._make_temp_file()
        try:
            out = StringIO()
            call_command(
                "ingest_media",
                user_id=str(self.user.id),
                files=[str(tmp_path)],
                stdout=out,
            )

            item = IngestItem.objects.get(user=self.user)
            self.assertEqual(item.item_type, "text")
            self.assertEqual(item.content_text, "Media diary entry")

            # One attachment
            files = ItemFile.objects.filter(user=self.user, item=item)
            self.assertEqual(files.count(), 1)
            self.assertEqual(files[0].role, FileRole.ATTACHMENT)
            self.assertEqual(files[0].filename, "tmp" + tmp_path.suffix)
            self.assertEqual(files[0].bytes, len(b"fake-image-data"))

            self.assertIn(str(item.id), out.getvalue())
            mock_classify.delay.assert_called_once()
        finally:
            tmp_path.unlink(missing_ok=True)

    @patch("src.classification.tasks.classify_item_task")
    def test_creates_entry_with_custom_text(self, mock_classify):
        tmp_path = self._make_temp_file()
        try:
            out = StringIO()
            call_command(
                "ingest_media",
                user_id=str(self.user.id),
                files=[str(tmp_path)],
                text="Fridge photo update",
                title="Kitchen",
                stdout=out,
            )

            item = IngestItem.objects.get(user=self.user)
            self.assertEqual(item.content_text, "Fridge photo update")
            self.assertEqual(item.title, "Kitchen")
        finally:
            tmp_path.unlink(missing_ok=True)

    @patch("src.classification.tasks.classify_item_task")
    def test_creates_entry_with_multiple_files(self, mock_classify):
        tmp1 = self._make_temp_file(suffix=".jpg")
        tmp2 = self._make_temp_file(suffix=".mp4", content=b"fake-video-data")
        try:
            call_command(
                "ingest_media",
                user_id=str(self.user.id),
                files=[str(tmp1), str(tmp2)],
            )

            item = IngestItem.objects.get(user=self.user)
            files = ItemFile.objects.filter(user=self.user, item=item)
            self.assertEqual(files.count(), 2)
            filenames = {f.filename for f in files}
            self.assertIn("tmp.jpg", filenames)
            self.assertIn("tmp.mp4", filenames)
        finally:
            tmp1.unlink(missing_ok=True)
            tmp2.unlink(missing_ok=True)

    def test_requires_user_id(self):
        with self.assertRaises(CommandError):
            call_command("ingest_media", files=["/tmp/x.jpg"])

    def test_requires_files(self):
        with self.assertRaises(CommandError):
            call_command("ingest_media", user_id=str(self.user.id))

    def test_file_not_found(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "ingest_media",
                user_id=str(self.user.id),
                files=["/tmp/nonexistent_xyz_file_42.jpg"],
            )
        self.assertIn("File not found", str(ctx.exception))
