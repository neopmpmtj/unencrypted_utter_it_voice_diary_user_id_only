"""Tests for local filesystem attachment/recording helpers."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock

from django.test import TestCase

from src.common.storage_local import (
    ensure_local_storage_tree,
    local_attachments_dir_for_item,
    local_recording_user_dir,
)
from src.common.utils.file_sys_utils import ensure_directory


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
