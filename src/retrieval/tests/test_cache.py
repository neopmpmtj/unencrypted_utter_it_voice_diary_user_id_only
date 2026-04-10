"""Tests for retrieval cache: write, read, delete, expiry."""

import json
import os
import tempfile
import time
from unittest.mock import patch

from django.test import TestCase

from src.retrieval import cache


class PrepCacheTests(TestCase):
    """Tests for the file-based prep cache."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self._patcher = patch.object(cache, "INDEX_CACHE_DIR", self.tmp_dir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_write_then_read(self):
        payload = {"entry_id": "abc", "content_text": "Hello world"}
        path = cache.write_prep_cache("abc", "user1", payload)
        result = cache.read_prep_cache(path)
        self.assertEqual(result, payload)

    def test_read_missing_file_returns_none(self):
        result = cache.read_prep_cache("/nonexistent/path/file.tmp")
        self.assertIsNone(result)

    def test_read_expired_returns_none(self):
        payload = {"entry_id": "exp"}
        path = cache.write_prep_cache("exp", "user1", payload)
        past = time.time() - cache.INDEX_CACHE_TTL - 10
        os.utime(path, (past, past))
        result = cache.read_prep_cache(path)
        self.assertIsNone(result)

    def test_delete_removes_file(self):
        payload = {"entry_id": "del"}
        path = cache.write_prep_cache("del", "user1", payload)
        self.assertTrue(os.path.exists(path))
        cache.delete_prep_cache(path)
        self.assertFalse(os.path.exists(path))

    def test_delete_missing_file_no_error(self):
        cache.delete_prep_cache("/nonexistent/path/file.tmp")

    def test_write_creates_user_subdirectory(self):
        cache.write_prep_cache("e1", "myuser", {"key": "val"})
        user_dir = os.path.join(self.tmp_dir, "myuser")
        self.assertTrue(os.path.isdir(user_dir))

    def test_read_corrupted_json_returns_none(self):
        base = os.path.join(self.tmp_dir, "t1")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "corrupt.tmp")
        with open(path, "w") as f:
            f.write("not valid json{{{")
        result = cache.read_prep_cache(path)
        self.assertIsNone(result)
