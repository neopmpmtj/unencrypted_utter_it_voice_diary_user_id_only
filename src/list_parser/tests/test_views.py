"""
Tests for list_parser views.

- Item DELETE: soft-delete item and descendants; soft-delete record when no top-level items remain
"""

from django.test import RequestFactory, TestCase

from src.accounts.models import CustomUser
from src.list_parser.models import ListItem, ListRecord, ListRecordStatus
from src.list_parser.views import list_item_api


class ListItemDeleteViewTests(TestCase):
    """Tests for list item DELETE API (soft-delete and record cascade)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = CustomUser.objects.create_user(
            email="listviewdel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_delete_last_top_level_item_soft_deletes_record(self):
        record = ListRecord.objects.create(
            user=self.user,
            created_by=self.user,
            list_name="test",
            status=ListRecordStatus.SUCCESS,
        )
        li = ListItem.objects.create(
            list_record=record, parent=None, item_index=0, text="only item"
        )

        request = self.factory.delete(f"/api/lists/{li.id}/item/")
        request.user = self.user

        response = list_item_api(request, item_id=str(li.id))

        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertEqual(ListItem.objects.filter(list_record=record).count(), 0)
        self.assertEqual(ListItem.all_objects.filter(list_record=record).count(), 1)
        li_refreshed = ListItem.all_objects.get(id=li.id)
        self.assertIsNotNone(li_refreshed.deleted_at)
