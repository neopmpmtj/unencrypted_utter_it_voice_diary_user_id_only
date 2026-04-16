"""
Tests for entries views: entry_delete_api, entries_list_api, and entries_page.
"""

import json
import re
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import CustomUser, GlobalSettings, UserPreferences
from src.batch_calendar.models import CalendarEvent, CalendarEventStatus
from src.classification.models import ItemClassificationRun, ItemClassificationSelection, ItemEntityLink
from src.ingestion.models import IngestItem, IngestItemEditLog, ItemFile, FileRole
from src.intent_router.models import ItemTriageResult
from src.managed_lists.models import ManagedRecordStatus, TodoItem, TodoRecord
from src.retrieval.models import ItemRetrievalProjection


class EntryDeleteApiTests(TestCase):
    """Tests for entry_delete_api (POST/DELETE api/entries/<id>/delete/)."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="entrydel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_entry_delete_api_204_soft_deletes_item_and_calendar(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        CalendarEvent.all_objects.create(
            user=self.user,
            source_item=item,
            summary="Test event",
            status=CalendarEventStatus.SUCCESS,
            google_event_id="gid",
        )

        url = reverse("entries:api_delete", args=[item.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 204)
        item.refresh_from_db()
        self.assertTrue(item.is_deleted)
        self.assertIsNotNone(item.deleted_at)
        for ev in CalendarEvent.all_objects.filter(source_item=item):
            self.assertTrue(ev.is_deleted)
            self.assertEqual(ev.status, CalendarEventStatus.CANCELLED)

    def test_entry_delete_api_soft_deletes_todo_records(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        record = TodoRecord.objects.create(
            user=self.user,
            source_item=item,
            created_by=None,
            status=ManagedRecordStatus.SUCCESS,
            record_name="Test",
        )
        ti = TodoItem.objects.create(
            todo_record=record,
            parent=None,
            item_index=0,
            text="Task",
            description="",
            priority=3,
            completion_status="open",
            item_data={},
        )

        url = reverse("entries:api_delete", args=[item.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 204)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertFalse(TodoItem.objects.filter(todo_record=record).exists())
        self.assertTrue(TodoItem.all_objects.get(id=ti.id).is_deleted)

    def test_entry_delete_api_soft_deletes_triage_and_classification(self):
        """Entry delete soft-deletes ItemTriageResult and ItemClassificationRun/Selection/EntityLink."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        triage = ItemTriageResult.objects.create(
            item=item,
            primary_route="event",
            confidence=0.9,
            contains_time_reference=True,
            contains_multiple_items=False,
            raw_output={},
        )
        run = ItemClassificationRun.objects.create(
            user=self.user,
            ingest_item=item,
            taxonomy_pack_used="personal",
            classifier_version="v14",
            prompt_version="v14",
            status="completed",
        )

        url = reverse("entries:api_delete", args=[item.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(ItemTriageResult.objects.filter(item=item).exists())
        self.assertTrue(ItemTriageResult.all_objects.get(id=triage.id).is_deleted)
        self.assertFalse(ItemClassificationRun.objects.filter(ingest_item=item).exists())
        self.assertTrue(ItemClassificationRun.all_objects.get(id=run.id).is_deleted)

    def test_entry_delete_api_404_wrong_entry(self):
        """Other user's entry returns 404 (cross-user isolation)."""
        other_user = CustomUser.objects.create_user(
            email="other@example.com",
            password="Pass123",
        )
        other_user.is_email_verified = True
        other_user.save()
        other_item = IngestItem.objects.create(
            user=other_user,
            item_type="text",
            is_deleted=False,
        )

        url = reverse("entries:api_delete", args=[other_item.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 404)
        other_item.refresh_from_db()
        self.assertFalse(other_item.is_deleted)

    def test_entry_delete_api_cascades_to_child_items(self):
        """Deleting parent soft-deletes child_items (edited copies)."""
        parent = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        child = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            parent_item=parent,
            is_deleted=False,
        )

        url = reverse("entries:api_delete", args=[parent.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 204)
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertTrue(parent.is_deleted)
        self.assertIsNotNone(parent.deleted_at)
        self.assertTrue(child.is_deleted)
        self.assertIsNotNone(child.deleted_at)

    def test_entry_delete_api_cascades_recursively(self):
        """Deleting A cascades to B and C when A -> B -> C."""
        item_a = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        item_b = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            parent_item=item_a,
            is_deleted=False,
        )
        item_c = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            parent_item=item_b,
            is_deleted=False,
        )

        url = reverse("entries:api_delete", args=[item_a.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 204)
        for it in [item_a, item_b, item_c]:
            it.refresh_from_db()
            self.assertTrue(it.is_deleted, f"Item {it.id} should be soft-deleted")
            self.assertIsNotNone(it.deleted_at)

    def test_entry_delete_api_cascades_to_split_children(self):
        """Deleting parent soft-deletes split_children."""
        parent = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        split1 = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            split_parent=parent,
            is_deleted=False,
        )
        split2 = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            split_parent=parent,
            is_deleted=False,
        )

        url = reverse("entries:api_delete", args=[parent.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 204)
        parent.refresh_from_db()
        split1.refresh_from_db()
        split2.refresh_from_db()
        self.assertTrue(parent.is_deleted)
        self.assertTrue(split1.is_deleted)
        self.assertTrue(split2.is_deleted)


class EntriesListApiTests(TestCase):
    """Tests for entries_list_api (GET /api/entries/)."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="entrieslist@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_entries_list_api_returns_attachments_with_storage_url(self):
        """Entry with ItemFile attachments includes them in API response with filename and storage_url."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="Voice Recording test",
            content_text="Test content",
        )
        ItemFile.objects.create(
            user=self.user,
            item=item,
            role=FileRole.ATTACHMENT,
            filename="Hello I would like to.txt",
            mime_type="text/plain",
            storage_url="https://drive.google.com/file/d/abc123/view?usp=drivesdk",
            bytes=1362,
        )

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn("entries", data)
        self.assertEqual(len(data["entries"]), 1)
        entry = data["entries"][0]
        self.assertIn("attachments", entry)
        self.assertEqual(len(entry["attachments"]), 1)
        att = entry["attachments"][0]
        self.assertEqual(att["filename"], "Hello I would like to.txt")
        self.assertEqual(att["storage_url"], "https://drive.google.com/file/d/abc123/view?usp=drivesdk")

    def test_entries_list_api_attachment_count_matches_db_for_modal_preview(self):
        """Entry with multiple attachments: API returns same count as DB for modal preview."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="Entry with 3 attachments",
            content_text="Content",
        )
        for i, (fname, url) in enumerate([
            ("img1.png", "https://drive.google.com/file/d/id1/view"),
            ("img2.jpg", "https://drive.google.com/file/d/id2/view"),
            ("doc.pdf", "https://example.com/doc.pdf"),
        ]):
            ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ATTACHMENT,
                filename=fname,
                mime_type="image/png" if fname.endswith((".png", ".jpg")) else "application/pdf",
                storage_url=url,
                bytes=1000,
            )

        db_count = item.files.filter(role=FileRole.ATTACHMENT).count()
        self.assertEqual(db_count, 3)

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 1)
        entry = data["entries"][0]
        api_attachment_count = len(entry["attachments"])
        self.assertEqual(
            api_attachment_count,
            db_count,
            "Attachment count in API response must match DB for modal preview",
        )

    def test_entries_list_api_attachment_with_empty_storage_url(self):
        """Entry with attachment pending Drive upload (storage_url empty) still returns filename."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="Voice Recording pending",
            content_text="Content",
        )
        ItemFile.objects.create(
            user=self.user,
            item=item,
            role=FileRole.ATTACHMENT,
            filename="doc.pdf",
            mime_type="application/pdf",
            storage_url="",
            bytes=5000,
        )

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        entry = data["entries"][0]
        att = entry["attachments"][0]
        self.assertEqual(att["filename"], "doc.pdf")
        self.assertEqual(att["storage_url"], "")

    def test_entries_list_api_attachment_local_filesystem_path_gets_download_url(self):
        """Non-HTTP storage_url is exposed as same-origin attachment download URL."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="audio",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="Local attach",
            content_text="Content",
        )
        att = ItemFile.objects.create(
            user=self.user,
            item=item,
            role=FileRole.ATTACHMENT,
            filename="photo.jpg",
            mime_type="image/jpeg",
            storage_url="/data/attachments/1/item/photo.jpg",
            bytes=10,
        )
        url = reverse("entries:api_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        att_out = data["entries"][0]["attachments"][0]
        self.assertEqual(att_out["filename"], "photo.jpg")
        self.assertEqual(att_out["id"], str(att.id))
        expected = "http://testserver" + reverse(
            "entries:serve_attachment", kwargs={"file_id": att.id}
        )
        self.assertEqual(att_out["storage_url"], expected)

    def test_entries_list_api_ids_param_returns_specified_entries_in_order(self):
        """GET ?ids=uuid1,uuid2 returns only those entries in requested order with content_full."""
        items = []
        for i in range(4):
            item = IngestItem.objects.create(
                user=self.user,
                item_type="text",
                status="processed",
                is_deleted=False,
                occurred_at=timezone.now() - timezone.timedelta(days=i),
                title=f"Entry {i}",
                content_text=f"Full content for entry {i}",
            )
            items.append(item)
        ids = [str(items[2].id), str(items[0].id), str(items[3].id)]
        url = reverse("entries:api_list") + "?ids=" + ",".join(ids)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 3)
        self.assertEqual(data["total_count"], 3)
        self.assertEqual(data["entries"][0]["id"], ids[0])
        self.assertEqual(data["entries"][1]["id"], ids[1])
        self.assertEqual(data["entries"][2]["id"], ids[2])
        for i, entry in enumerate(data["entries"]):
            self.assertIn("content_full", entry)
            self.assertEqual(entry["content_full"], f"Full content for entry {[2, 0, 3][i]}")

    def test_entries_list_api_ids_param_returns_empty_when_none_match(self):
        """GET ?ids=invalid-uuid returns empty entries."""
        url = reverse("entries:api_list") + "?ids=00000000-0000-0000-0000-000000000000"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["entries"], [])
        self.assertEqual(data["total_count"], 0)

    def test_entries_list_api_ids_param_limits_to_20(self):
        """GET ?ids=... with more than 20 IDs only fetches first 20."""
        items = []
        for i in range(25):
            item = IngestItem.objects.create(
                user=self.user,
                item_type="text",
                status="processed",
                is_deleted=False,
                occurred_at=timezone.now(),
                title=f"Entry {i}",
                content_text=f"Content {i}",
            )
            items.append(item)
        ids = [str(items[i].id) for i in range(25)]
        url = reverse("entries:api_list") + "?ids=" + ",".join(ids)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 20)

    def _create_entries(self, count, user):
        """Create count entries with distinct occurred_at for ordering."""
        base = timezone.now()
        for i in range(count):
            IngestItem.objects.create(
                user=user,
                item_type="text",
                status="processed",
                is_deleted=False,
                occurred_at=base - timezone.timedelta(minutes=i),
                title=f"Entry {i}",
                content_text=f"Content {i}",
            )

    @patch("src.entries.views.get_max_browse_entries", return_value=5)
    def test_entries_list_api_max_browse_limit_caps_results(self, mock_get_max):
        """Non-search returns at most max_browse_entries when limit is set."""
        self._create_entries(30, self.user)

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 5)
        self.assertFalse(data["has_more"])

    @patch("src.entries.views.get_max_browse_entries", return_value=0)
    def test_entries_list_api_max_browse_unlimited_when_zero(self, mock_get_max):
        """Non-search returns all entries when max_browse_entries is 0 (unlimited)."""
        self._create_entries(25, self.user)

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 20)
        self.assertTrue(data["has_more"])
        self.assertIsNotNone(data["next_cursor"])

        response2 = self.client.get(url, {"cursor": data["next_cursor"]})
        data2 = json.loads(response2.content)
        self.assertEqual(len(data2["entries"]), 5)
        self.assertFalse(data2["has_more"])

    def test_entries_list_api_default_allows_unlimited_browse(self):
        """Default (max_browse=0) allows pagination beyond first page."""
        self._create_entries(25, self.user)

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 20)
        self.assertTrue(data["has_more"])
        self.assertIsNotNone(data["next_cursor"])

        response2 = self.client.get(url, {"cursor": data["next_cursor"]})
        data2 = json.loads(response2.content)
        self.assertEqual(len(data2["entries"]), 5)
        self.assertFalse(data2["has_more"])

    def test_entries_list_api_max_browse_uses_globalsettings(self):
        """Max browse limit comes from GlobalSettings when set."""
        GlobalSettings.set_value("entries.max_browse_entries", 3)
        self._create_entries(10, self.user)

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 3)
        self.assertFalse(data["has_more"])

    @patch("src.financial_parser.services.get_financial_display_content")
    @patch("src.list_parser.services.get_list_display_content")
    def test_entries_list_api_does_not_call_display_formatters(self, mock_list_disp, mock_fin_disp):
        """Listing entries must never invoke the LLM formatters; content_text is pre-stored."""
        IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="Despesas",
            content_text="Despesas\n- café: 3 EUR",
        )

        url = reverse("entries:api_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["entries"]), 1)
        mock_fin_disp.assert_not_called()
        mock_list_disp.assert_not_called()


class ServeAttachmentTests(TestCase):
    """Tests for GET /api/attachments/<id>/download/."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="serveattach@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_serve_attachment_streams_local_file(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            uid = str(self.user.id)
            att_dir = tmp / "attachments" / uid
            att_dir.mkdir(parents=True)
            file_path = att_dir / "doc.txt"
            file_path.write_bytes(b"hello attachment")
            item = IngestItem.objects.create(
                user=self.user,
                item_type="text",
                status="processed",
                is_deleted=False,
                occurred_at=timezone.now(),
                title="T",
                content_text="C",
            )
            item_file = ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ATTACHMENT,
                filename="doc.txt",
                mime_type="text/plain",
                storage_url=str(file_path.resolve()),
                bytes=len(b"hello attachment"),
            )
            cfg = SimpleNamespace(
                storage=SimpleNamespace(
                    save_attachments_to_local_filesystem=True,
                    local_storage_root=str(tmp.resolve()),
                    local_attachments_subdir="attachments",
                    local_recordings_subdir="recordings",
                )
            )
            with patch("src.entries.views.get_config", return_value=cfg):
                dl = reverse("entries:serve_attachment", kwargs={"file_id": item_file.id})
                response = self.client.get(dl)
            self.assertEqual(response.status_code, 200)
            body = b"".join(response.streaming_content)
            self.assertEqual(body, b"hello attachment")
        finally:
            shutil.rmtree(tmp)

    def test_serve_attachment_404_when_path_outside_allowed_tree(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "evil.txt").write_bytes(b"x")
            item = IngestItem.objects.create(
                user=self.user,
                item_type="text",
                status="processed",
                is_deleted=False,
                occurred_at=timezone.now(),
                title="T",
                content_text="C",
            )
            item_file = ItemFile.objects.create(
                user=self.user,
                item=item,
                role=FileRole.ATTACHMENT,
                filename="evil.txt",
                mime_type="text/plain",
                storage_url=str((tmp / "evil.txt").resolve()),
                bytes=1,
            )
            cfg = SimpleNamespace(
                storage=SimpleNamespace(
                    save_attachments_to_local_filesystem=True,
                    local_storage_root=str(tmp.resolve()),
                    local_attachments_subdir="attachments",
                    local_recordings_subdir="recordings",
                )
            )
            with patch("src.entries.views.get_config", return_value=cfg):
                dl = reverse("entries:serve_attachment", kwargs={"file_id": item_file.id})
                response = self.client.get(dl)
            self.assertEqual(response.status_code, 404)
        finally:
            shutil.rmtree(tmp)

    def test_serve_attachment_redirects_for_https_storage_url(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title="T",
            content_text="C",
        )
        target = "https://drive.example.com/file"
        item_file = ItemFile.objects.create(
            user=self.user,
            item=item,
            role=FileRole.ATTACHMENT,
            filename="remote.pdf",
            mime_type="application/pdf",
            storage_url=target,
            bytes=1,
        )
        dl = reverse("entries:serve_attachment", kwargs={"file_id": item_file.id})
        response = self.client.get(dl)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], target)


class EntryEditApiTests(TestCase):
    """Tests for entry_edit_api (POST /api/entries/<id>/edit/)."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="entryedit@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "pro"
        self.user.save()
        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def _create_entry(self, title="Test", content="Content", tags=None):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status="processed",
            is_deleted=False,
            occurred_at=timezone.now(),
            title=title,
            content_text=content,
            summary_text="",
        )
        return item

    def test_entry_edit_api_overwrite_updates_content_and_creates_log(self):
        """Overwrite mode updates item and creates IngestItemEditLog."""
        item = self._create_entry(title="Original", content="Original content")
        url = reverse("entries:api_edit", args=[item.id])
        payload = json.dumps({
            "content_text": "Updated content",
            "title": "Updated title",
            "tags": ["tag1"],
            "create_new": False,
        })
        response = self.client.post(
            url, payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data["created_new"])
        self.assertEqual(data["entry"]["title"], "Updated title")
        self.assertEqual(data["entry"]["content_full"], "Updated content")
        self.assertIsInstance(data["entry"]["tags"], list)

        item.refresh_from_db()
        log = IngestItemEditLog.objects.filter(item=item).first()
        self.assertIsNotNone(log)
        self.assertIn("content_text", log.fields_changed)
        self.assertIn("title", log.fields_changed)

    def test_entry_edit_api_create_new_links_to_parent(self):
        """Create-new mode creates linked copy with parent_item."""
        item = self._create_entry(title="Original", content="Original")
        url = reverse("entries:api_edit", args=[item.id])
        payload = json.dumps({
            "content_text": "Forked content",
            "title": "Forked title",
            "tags": [],
            "create_new": True,
        })
        response = self.client.post(
            url, payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["created_new"])
        self.assertEqual(data["entry"]["id"], str(IngestItem.objects.latest("ingested_at").id))
        self.assertNotEqual(data["entry"]["id"], str(item.id))

        new_item = IngestItem.objects.get(id=data["entry"]["id"])
        self.assertEqual(new_item.parent_item_id, item.id)
        self.assertEqual(new_item.occurred_at, item.occurred_at)

    def test_entry_edit_api_404_wrong_entry(self):
        """Other user's entry returns 404 (cross-user isolation)."""
        other = CustomUser.objects.create_user(
            email="other@example.com", password="Pass123"
        )
        other.is_email_verified = True
        other.save()
        other_item = IngestItem.objects.create(
            user=other,
            item_type="text",
            is_deleted=False,
            title="Other",
            content_text="Other",
        )
        url = reverse("entries:api_edit", args=[other_item.id])
        payload = json.dumps({
            "content_text": "x",
            "title": "x",
            "tags": [],
            "create_new": False,
        })
        response = self.client.post(
            url, payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 404)

    @patch("src.entries.views.classify_item_task")
    def test_entry_edit_api_in_place_edit_queues_classification(self, mock_classify):
        """In-place edit of a generic entry re-queues classify_item_task."""
        item = self._create_entry(title="Original", content="Original content")
        url = reverse("entries:api_edit", args=[item.id])
        payload = json.dumps({
            "content_text": "Updated content for re-classification",
            "title": "Updated title",
            "tags": [],
            "create_new": False,
        })
        response = self.client.post(
            url, payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data["created_new"])
        mock_classify.delay.assert_called_once()
        call_args = mock_classify.delay.call_args[0]
        self.assertEqual(call_args[0], str(item.id))
        self.assertEqual(call_args[1], "Updated content for re-classification")
        self.assertEqual(call_args[2], item.detected_language or "")

    @patch("src.entries.views.classify_item_task")
    def test_entry_edit_api_create_new_queues_classification_for_new_item(self, mock_classify):
        """Create-as-new queues classify_item_task for the new record."""
        item = self._create_entry(title="Original", content="Original")
        url = reverse("entries:api_edit", args=[item.id])
        payload = json.dumps({
            "content_text": "Forked content",
            "title": "Forked title",
            "tags": [],
            "create_new": True,
        })
        response = self.client.post(
            url, payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["created_new"])
        new_item_id = data["entry"]["id"]
        self.assertNotEqual(new_item_id, str(item.id))
        mock_classify.delay.assert_called_once()
        call_args = mock_classify.delay.call_args[0]
        self.assertEqual(call_args[0], new_item_id)
        self.assertEqual(call_args[1], "Forked content")
        self.assertEqual(call_args[2], item.detected_language or "")

    @patch("src.entries.views.parse_batch_calendar_task")
    def test_entry_edit_api_returns_calendar_parsing_queued_when_calendar_entry(self, mock_calendar_task):
        """When editing a calendar-classified entry, response includes calendar_parsing_queued for frontend conflict listening."""
        item = self._create_entry(title="Appointment", content="Book physio Monday 3pm")
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.reminder.future.followup",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )
        url = reverse("entries:api_edit", args=[item.id])
        payload = json.dumps({
            "content_text": "Book physio Monday 3pm and Tuesday 2pm",
            "title": "Appointments",
            "tags": [],
            "create_new": False,
        })
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data.get("calendar_parsing_queued"))
        mock_calendar_task.delay.assert_called_once()

    @patch("src.entries.views.get_config")
    @patch("src.entries.views.verify_drive_permissions")
    @patch("src.entries.views.upload_local_file_to_user_drive_folder")
    def test_entry_edit_api_multipart_attachments_count_matches_db(
        self, mock_upload_drive, mock_verify, mock_get_config
    ):
        """Edit with attachments: number of ItemFile records equals number of files uploaded."""
        mock_get_config.return_value.storage.audio_temp_path = tempfile.gettempdir()
        mock_verify.return_value = True
        mock_upload_drive.return_value = {
            "id": "drive-1",
            "name": "doc.pdf",
            "webViewLink": "https://drive.google.com/file/d/drive-1/view",
            "parent_folder_id": "folder-123",
        }

        item = self._create_entry(title="Original", content="Original content")
        url = reverse("entries:api_edit", args=[item.id])

        f1 = SimpleUploadedFile("attach1.pdf", b"x" * 500, content_type="application/pdf")
        f2 = SimpleUploadedFile("attach2.txt", b"y" * 300, content_type="text/plain")
        f3 = SimpleUploadedFile("attach3.pdf", b"z" * 400, content_type="application/pdf")

        multipart_data = {
            "content_text": "Updated with 3 attachments",
            "title": "Updated title",
            "create_new": "false",
            "files": [f1, f2, f3],
        }
        response = self.client.post(url, data=multipart_data)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data["created_new"])
        self.assertEqual(data.get("attachment_count"), 3)

        item.refresh_from_db()
        attachments = ItemFile.objects.filter(item=item, role=FileRole.ATTACHMENT)
        self.assertEqual(attachments.count(), 3, "DB attachment count must match files uploaded")

    @patch("src.entries.views.get_config")
    def test_entry_edit_api_multipart_local_filesystem_attachments(
        self, mock_get_config
    ):
        """When local filesystem storage is enabled, attachments stay on disk with absolute paths."""
        root = Path(tempfile.mkdtemp())
        try:
            mock_storage = mock_get_config.return_value.storage
            mock_storage.audio_temp_path = tempfile.gettempdir()
            mock_storage.save_attachments_to_local_filesystem = True
            mock_storage.local_storage_root = str(root)
            mock_storage.local_attachments_subdir = "attachments"
            mock_storage.local_recordings_subdir = "recordings"

            item = self._create_entry(title="Original", content="Original content")
            url = reverse("entries:api_edit", args=[item.id])
            f1 = SimpleUploadedFile(
                "local1.pdf", b"a" * 100, content_type="application/pdf"
            )
            multipart_data = {
                "content_text": "Updated with local attachment",
                "title": "Updated",
                "create_new": "false",
                "files": [f1],
            }
            response = self.client.post(url, data=multipart_data)
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)
            self.assertEqual(data.get("attachment_count"), 1)
            att = ItemFile.objects.filter(item=item, role=FileRole.ATTACHMENT).first()
            self.assertIsNotNone(att)
            self.assertTrue(Path(att.storage_url).is_absolute())
            self.assertTrue(Path(att.storage_url).exists())
            self.assertEqual(att.drive_folder_id, "")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class EntriesPageTests(TestCase):
    """Tests for entries_page (entries list with edit modal and recorder config)."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("entries:list")

    def _create_user(self, email="entriespage@example.com", tier="free"):
        user = CustomUser.objects.create_user(
            email=email,
            password="Pass123",
        )
        user.is_email_verified = True
        user.tier = tier
        user.save()
        prefs = UserPreferences.objects.get(user=user)
        prefs.onboarding_completed = True
        prefs.save()
        return user

    def _get_recorder_config(self, response):
        match = re.search(
            r'<script[^>]*id="recorder-config"[^>]*>(.*?)</script>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        return json.loads(match.group(1).strip())

    def test_entries_page_includes_recorder_config(self):
        """All users get recorder_config (Record button in edit modal)."""
        user = self._create_user(tier="free")
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        config = self._get_recorder_config(response)
        self.assertIn("uploadUrl", config)
        self.assertIn("maxDuration", config)
        self.assertIn("maxFileSize", config)
        self.assertIn("showTimer", config)

    def test_entries_page_show_timer_always_true(self):
        """recorder_config.showTimer is always True for edit modal (timer always visible)."""
        user = self._create_user(tier="free")
        prefs = UserPreferences.objects.get(user=user)
        prefs.show_recording_timer = False
        prefs.save()
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        config = self._get_recorder_config(response)
        self.assertTrue(config.get("showTimer"))
