"""
Tests for managed lists todo delete API: TodoRecord soft-delete when last item removed.
"""

from django.test import Client, TestCase
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences
from src.managed_lists.models import (
    ManagedRecordStatus,
    TodoCompletionStatus,
    TodoItem,
    TodoPriority,
    TodoRecord,
)


class TodoDeleteApiTests(TestCase):
    """Tests for todo_item_api DELETE and todos_bulk_api delete: TodoRecord soft-delete."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email="tododel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        self.client.force_login(self.user)

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def _create_record_with_items(self, item_count, source_item=None):
        record = TodoRecord.objects.create(
            user=self.user,
            source_item=source_item,
            created_by=self.user if not source_item else None,
            status=ManagedRecordStatus.SUCCESS,
            record_name="Test",
        )
        items = []
        for i in range(item_count):
            ti = TodoItem.objects.create(
                todo_record=record,
                parent=None,
                item_index=i,
                text=f"Task {i}",
                description="",
                priority=TodoPriority.MEDIUM,
                completion_status=TodoCompletionStatus.OPEN,
                item_data={},
            )
            items.append(ti)
        return record, items

    def test_single_delete_last_item_soft_deletes_record(self):
        record, [ti] = self._create_record_with_items(1)
        url = reverse("managed_lists:api_todo_item", args=[ti.id])

        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TodoItem.objects.filter(id=ti.id).exists())
        self.assertTrue(TodoItem.all_objects.filter(id=ti.id).exists())
        self.assertTrue(TodoItem.all_objects.get(id=ti.id).is_deleted)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)

    def test_single_delete_parent_with_children_soft_deletes_record(self):
        record, [parent] = self._create_record_with_items(1)
        child = TodoItem.objects.create(
            todo_record=record,
            parent=parent,
            item_index=0,
            text="Subtask",
            description="",
            priority=TodoPriority.MEDIUM,
            completion_status=TodoCompletionStatus.OPEN,
            item_data={},
        )
        url = reverse("managed_lists:api_todo_item", args=[parent.id])

        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TodoItem.objects.filter(id=parent.id).exists())
        self.assertFalse(TodoItem.objects.filter(id=child.id).exists())
        self.assertTrue(TodoItem.all_objects.get(id=parent.id).is_deleted)
        self.assertTrue(TodoItem.all_objects.get(id=child.id).is_deleted)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)

    def test_single_delete_not_last_item_record_unchanged(self):
        record, items = self._create_record_with_items(2)
        ti_to_delete = items[0]
        url = reverse("managed_lists:api_todo_item", args=[ti_to_delete.id])

        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TodoItem.objects.filter(id=ti_to_delete.id).exists())
        self.assertTrue(TodoItem.all_objects.get(id=ti_to_delete.id).is_deleted)
        self.assertTrue(TodoItem.objects.filter(id=items[1].id).exists())
        record.refresh_from_db()
        self.assertFalse(record.is_deleted)
        self.assertIsNone(record.deleted_at)

    def test_bulk_delete_all_items_soft_deletes_record(self):
        record, items = self._create_record_with_items(2)
        url = reverse("managed_lists:api_todos_bulk")
        payload = {"action": "delete", "item_ids": [str(t.id) for t in items]}

        response = self.client.post(
            url,
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TodoItem.objects.filter(todo_record=record).exists())
        for t in items:
            self.assertTrue(TodoItem.all_objects.get(id=t.id).is_deleted)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)

    def test_bulk_delete_partial_record_unchanged(self):
        record, items = self._create_record_with_items(2)
        url = reverse("managed_lists:api_todos_bulk")
        payload = {"action": "delete", "item_ids": [str(items[0].id)]}

        response = self.client.post(
            url,
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TodoItem.objects.filter(id=items[0].id).exists())
        self.assertTrue(TodoItem.all_objects.get(id=items[0].id).is_deleted)
        self.assertTrue(TodoItem.objects.filter(id=items[1].id).exists())
        record.refresh_from_db()
        self.assertFalse(record.is_deleted)
        self.assertIsNone(record.deleted_at)

    def test_record_delete_soft_deletes_items(self):
        """todo_record_api DELETE soft-deletes TodoRecord and all its TodoItems."""
        record, items = self._create_record_with_items(2)
        url = reverse("managed_lists:api_todo_record", args=[record.id])

        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertFalse(TodoItem.objects.filter(todo_record=record).exists())
        for ti in items:
            self.assertTrue(TodoItem.all_objects.get(id=ti.id).is_deleted)
