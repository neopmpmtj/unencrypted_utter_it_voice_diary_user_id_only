"""Tests for intent_router.models."""

import uuid

from django.test import TestCase

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem
from src.intent_router.models import ItemTriageResult


class ItemTriageResultModelTests(TestCase):
    """Tests for ItemTriageResult model."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="triage@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_item_triage_result_creation(self):
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
            raw_output={"primary_route": "event"},
        )
        self.assertIsInstance(triage.id, uuid.UUID)
        self.assertIsNotNone(triage.created_at)
        self.assertEqual(triage.primary_route, "event")
        self.assertEqual(triage.confidence, 0.9)
        self.assertTrue(triage.contains_time_reference)


class SoftDeleteManagerTests(TestCase):
    """Tests for SoftDeleteManager."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="softdel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_soft_delete_manager_excludes_deleted(self):
        item1 = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        item2 = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        triage1 = ItemTriageResult.objects.create(
            item=item1,
            primary_route="note",
            confidence=0.8,
            contains_time_reference=False,
            contains_multiple_items=False,
        )
        triage2 = ItemTriageResult.objects.create(
            item=item2,
            primary_route="note",
            confidence=0.8,
            contains_time_reference=False,
            contains_multiple_items=False,
        )
        triage2.is_deleted = True
        triage2.save()
        self.assertEqual(ItemTriageResult.objects.count(), 1)
        self.assertEqual(ItemTriageResult.all_objects.count(), 2)
        self.assertIn(triage1, ItemTriageResult.objects.all())
        self.assertNotIn(triage2, ItemTriageResult.objects.all())


class ItemTriageResultStrTests(TestCase):
    """Tests for ItemTriageResult.__str__."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="strtest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_str_representation(self):
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
        )
        s = str(triage)
        self.assertIn(str(item.id), s)
        self.assertIn("event", s)
        self.assertIn("0.90", s)


class ItemTriageResultCascadeTests(TestCase):
    """Tests for ItemTriageResult OneToOne cascade."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="cascade@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_one_to_one_cascade_delete(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            is_deleted=False,
        )
        triage = ItemTriageResult.objects.create(
            item=item,
            primary_route="note",
            confidence=0.5,
            contains_time_reference=False,
            contains_multiple_items=False,
        )
        item_id = item.id
        triage_id = triage.id
        item.delete()
        self.assertFalse(ItemTriageResult.all_objects.filter(id=triage_id).exists())


class ItemTriageResultIndexTests(TestCase):
    """Tests for ItemTriageResult indexes."""

    def test_indexes_exist(self):
        indexes = ItemTriageResult._meta.indexes
        index_fields = [frozenset(idx.fields) for idx in indexes]
        self.assertIn(frozenset(["primary_route"]), index_fields)
        self.assertIn(frozenset(["is_deleted"]), index_fields)
