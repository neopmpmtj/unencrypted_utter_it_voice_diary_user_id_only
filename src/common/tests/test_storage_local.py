"""Tests for local filesystem attachment/recording helpers."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock

from django.test import TestCase

from src.common.storage_local import (
    allocate_unique_attachment_filename,
    ensure_local_storage_tree,
    is_audio_storage_path_allowed_for_user,
    local_attachments_dir_for_item,
    local_recording_user_dir,
)
from src.common.utils.file_sys_utils import ensure_directory


class IsAudioStoragePathAllowedTests(TestCase):
    def test_returns_false_when_temp_audio_path_is_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "audio_temp"
            blocker.write_text("x", encoding="utf-8")
            cfg = MagicMock()
            cfg.storage.audio_temp_path = str(blocker)
            cfg.storage.save_attachments_to_local_filesystem = False
            fp = Path(tmp) / "other" / "a.webm"
            self.assertFalse(
                is_audio_storage_path_allowed_for_user(cfg, fp, 1)
            )

    def test_does_not_create_missing_temp_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio_root = Path(tmp) / "absent_audio_root"
            self.assertFalse(audio_root.exists())
            cfg = MagicMock()
            cfg.storage.audio_temp_path = str(audio_root)
            cfg.storage.save_attachments_to_local_filesystem = False
            fp = audio_root / "1" / "rec.webm"
            self.assertTrue(
                is_audio_storage_path_allowed_for_user(cfg, fp, 1)
            )
            self.assertFalse(
                audio_root.exists(),
                "validation must not mkdir the configured temp root",
            )

    def test_true_when_file_under_resolved_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio_root = Path(tmp) / "audio"
            audio_root.mkdir()
            fp = audio_root / "9" / "x.webm"
            fp.parent.mkdir()
            fp.write_bytes(b"x")
            cfg = MagicMock()
            cfg.storage.audio_temp_path = str(audio_root)
            cfg.storage.save_attachments_to_local_filesystem = False
            self.assertTrue(
                is_audio_storage_path_allowed_for_user(cfg, fp, 9)
            )

    def test_local_recordings_dir_is_file_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            root.mkdir()
            uid = 3
            rec_parent = root / "recordings"
            rec_parent.mkdir()
            user_rec = rec_parent / str(uid)
            user_rec.write_text("blocked", encoding="utf-8")
            cfg = MagicMock()
            cfg.storage.audio_temp_path = str(Path(tmp) / "unused_audio")
            cfg.storage.save_attachments_to_local_filesystem = True
            cfg.storage.local_storage_root = str(root)
            cfg.storage.local_recordings_subdir = "recordings"
            fp = root / "recordings" / str(uid) / "x.webm"
            self.assertFalse(
                is_audio_storage_path_allowed_for_user(cfg, fp, uid)
            )


class AllocateUniqueAttachmentFilenameTests(TestCase):
    def test_same_batch_does_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            used: set[str] = set()
            self.assertEqual(
                allocate_unique_attachment_filename(d, "doc.pdf", used), "doc.pdf"
            )
            self.assertEqual(
                allocate_unique_attachment_filename(d, "doc.pdf", used), "doc_1.pdf"
            )
            self.assertEqual(
                allocate_unique_attachment_filename(d, "doc.pdf", used), "doc_2.pdf"
            )
            self.assertEqual(len(used), 3)

    def test_respects_existing_file_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.txt").write_text("x", encoding="utf-8")
            used: set[str] = set()
            self.assertEqual(
                allocate_unique_attachment_filename(d, "a.txt", used), "a_1.txt"
            )


class EnsureDirectoryStrictTestCase(TestCase):
    def test_rejects_existing_non_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "not_a_dir"
            blocker.write_text("x", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                ensure_directory(blocker)


class StorageLocalTreeTestCase(TestCase):
    def test_ensure_local_storage_tree_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            cfg = MagicMock()
            cfg.storage.save_attachments_to_local_filesystem = True
            cfg.storage.local_storage_root = str(root)
            cfg.storage.local_attachments_subdir = "attachments"
            cfg.storage.local_recordings_subdir = "recordings"
            ensure_local_storage_tree(cfg)
            ensure_local_storage_tree(cfg)
            self.assertTrue((root / "attachments").is_dir())
            self.assertTrue((root / "recordings").is_dir())

    def test_local_attachments_dir_for_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            cfg = MagicMock()
            cfg.storage.save_attachments_to_local_filesystem = True
            cfg.storage.local_storage_root = str(root)
            cfg.storage.local_attachments_subdir = "attachments"
            cfg.storage.local_recordings_subdir = "recordings"
            uid = 42
            iid = uuid.uuid4()
            d = local_attachments_dir_for_item(cfg, uid, iid)
            self.assertTrue(d.is_dir())
            self.assertIn("attachments", d.parts)
            self.assertIn(str(uid), d.parts)
            self.assertIn(str(iid), d.parts)

    def test_local_recording_user_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            cfg = MagicMock()
            cfg.storage.save_attachments_to_local_filesystem = True
            cfg.storage.local_storage_root = str(root)
            cfg.storage.local_attachments_subdir = "attachments"
            cfg.storage.local_recordings_subdir = "recordings"
            d = local_recording_user_dir(cfg, 99)
            self.assertTrue(d.is_dir())
            self.assertIn("recordings", d.parts)
            self.assertIn("99", d.parts)
