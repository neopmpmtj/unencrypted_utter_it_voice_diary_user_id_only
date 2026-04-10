"""Tests for classification services: get_parser_routes_for_run, has_calendar_classification."""

from django.test import TestCase

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem
from src.retrieval.models import ItemRetrievalProjection

from src.classification.models import (
    ItemClassificationRun,
    ItemClassificationSelection,
    TaxonomyNode,
)
from src.classification.services import (
    get_parser_routes_for_run,
    has_calendar_classification,
    has_financial_classification,
    has_list_classification,
    has_todo_classification,
)


def _get_node(key: str) -> TaxonomyNode:
    return TaxonomyNode.objects.get(key=key)


def _create_run_with_selections(item: IngestItem, selections: list[tuple[str, str]]) -> ItemClassificationRun:
    run = ItemClassificationRun.objects.create(
        user=item.user,
        ingest_item=item,
        taxonomy_pack_used="personal",
        classifier_version="v14.1",
        prompt_version="v14.1",
        status="completed",
    )
    for i, (dim, path_key) in enumerate(selections):
        node = _get_node(path_key)
        ItemClassificationSelection.objects.create(
            classification_run=run,
            ingest_item=item,
            dimension=dim,
            taxonomy_node=node,
            path_key=path_key,
            is_primary=(i == 0),
            rank_order=i + 1,
        )
    return run


class GetParserRoutesForRunTests(TestCase):
    """Tests for get_parser_routes_for_run calendar route resolution."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="parser_routes@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_calendar_intent_reminder(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        run = _create_run_with_selections(item, [
            ("intent", "intent.reminder.future.followup"),
            ("subject", "personal.daily.diary"),
            ("context", "context.self.daily.routine"),
            ("governance", "gov.personal.private.self_only"),
        ])
        actions = get_parser_routes_for_run(run)
        self.assertIn("calendar", actions)

    def test_calendar_intent_todo(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        run = _create_run_with_selections(item, [
            ("intent", "intent.task.create.todo"),
            ("subject", "personal.daily.diary"),
            ("context", "context.self.daily.routine"),
            ("governance", "gov.personal.private.self_only"),
        ])
        actions = get_parser_routes_for_run(run)
        self.assertIn("todo", actions)
        self.assertNotIn("calendar", actions)

    def test_calendar_intent_reschedule(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        run = _create_run_with_selections(item, [
            ("intent", "intent.task.modify.reschedule"),
            ("subject", "personal.daily.diary"),
            ("context", "context.self.daily.routine"),
            ("governance", "gov.personal.private.self_only"),
        ])
        actions = get_parser_routes_for_run(run)
        self.assertIn("calendar", actions)

    def test_calendar_subject_appointment(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        run = _create_run_with_selections(item, [
            ("intent", "intent.capture.note.freeform"),
            ("subject", "personal.health.appointment.dentist"),
            ("context", "context.self.daily.routine"),
            ("governance", "gov.personal.private.self_only"),
        ])
        actions = get_parser_routes_for_run(run)
        self.assertIn("calendar", actions)

    def test_calendar_subject_appointment_migration_0003(self):
        """Validates migration 0003: subject personal.health.appointment.* -> calendar.
        Freeform note with appointment subject triggers calendar even without todo intent."""
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        run = _create_run_with_selections(item, [
            ("intent", "intent.capture.note.freeform"),
            ("subject", "personal.health.appointment.dentist"),
            ("context", "context.self.daily.routine"),
            ("governance", "gov.personal.private.self_only"),
        ])
        actions = get_parser_routes_for_run(run)
        self.assertIn("calendar", actions)

    def test_no_calendar_for_diary_note(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        run = _create_run_with_selections(item, [
            ("intent", "intent.capture.note.freeform"),
            ("subject", "personal.daily.diary"),
            ("context", "context.self.daily.routine"),
            ("governance", "gov.personal.private.self_only"),
        ])
        actions = get_parser_routes_for_run(run)
        self.assertNotIn("calendar", actions)


class HasCalendarClassificationTests(TestCase):
    """Tests for has_calendar_classification."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="has_calendar@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def _create_item_with_projection(self, primary_intent: str, primary_subject: str) -> IngestItem:
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key=primary_intent,
            primary_subject_key=primary_subject,
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )
        return item

    def test_has_calendar_intent_reminder(self):
        item = self._create_item_with_projection(
            "intent.reminder.future.followup",
            "personal.daily.diary",
        )
        self.assertTrue(has_calendar_classification(item))

    def test_has_calendar_intent_todo(self):
        item = self._create_item_with_projection(
            "intent.task.create.todo",
            "personal.daily.diary",
        )
        self.assertFalse(has_calendar_classification(item))

    def test_has_calendar_intent_reschedule(self):
        item = self._create_item_with_projection(
            "intent.task.modify.reschedule",
            "personal.daily.diary",
        )
        self.assertTrue(has_calendar_classification(item))

    def test_has_calendar_subject_appointment(self):
        item = self._create_item_with_projection(
            "intent.capture.note.freeform",
            "personal.health.appointment.dentist",
        )
        self.assertTrue(has_calendar_classification(item))

    def test_has_calendar_false_diary_note(self):
        item = self._create_item_with_projection(
            "intent.capture.note.freeform",
            "personal.daily.diary",
        )
        self.assertFalse(has_calendar_classification(item))

    def test_has_calendar_false_no_projection(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )
        self.assertFalse(has_calendar_classification(item))


class TriageFastPathTests(TestCase):
    """Tests that has_*_classification() uses triage fast-path when available."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="triage_fastpath@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def _create_item(self) -> IngestItem:
        return IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )

    def _attach_triage(self, item: IngestItem, route: str):
        from src.intent_router.models import ItemTriageResult
        ItemTriageResult.objects.create(
            item=item,
            primary_route=route,
            confidence=0.95,
        )

    def test_triage_event_returns_calendar_true(self):
        item = self._create_item()
        self._attach_triage(item, "event")
        self.assertTrue(has_calendar_classification(item))

    def test_triage_collection_returns_list_true(self):
        item = self._create_item()
        self._attach_triage(item, "collection")
        self.assertTrue(has_list_classification(item))

    def test_triage_finance_returns_financial_true(self):
        item = self._create_item()
        self._attach_triage(item, "finance")
        self.assertTrue(has_financial_classification(item))

    def test_triage_note_falls_through_to_projection_for_calendar(self):
        item = self._create_item()
        self._attach_triage(item, "note")
        self.assertFalse(has_calendar_classification(item))

    def test_triage_task_falls_through_for_list(self):
        item = self._create_item()
        self._attach_triage(item, "task")
        self.assertFalse(has_list_classification(item))

    def test_no_triage_no_projection_returns_false(self):
        item = self._create_item()
        self.assertFalse(has_calendar_classification(item))
        self.assertFalse(has_list_classification(item))
        self.assertFalse(has_financial_classification(item))

    def test_no_triage_with_projection_uses_projection(self):
        item = self._create_item()
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.reminder.future.followup",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )
        self.assertTrue(has_calendar_classification(item))

    def test_triage_task_returns_todo_true(self):
        item = self._create_item()
        self._attach_triage(item, "task")
        self.assertTrue(has_todo_classification(item))

    def test_list_projection_intent_list(self):
        item = self._create_item()
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.capture.note.list",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )
        self.assertTrue(has_list_classification(item))

    def test_financial_projection_subject_expense(self):
        item = self._create_item()
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.capture.note.freeform",
            primary_subject_key="personal.finance.expense.groceries",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )
        self.assertTrue(has_financial_classification(item))


class HasTodoClassificationTests(TestCase):
    """Tests for has_todo_classification."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="has_todo@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def _create_item(self) -> IngestItem:
        return IngestItem.objects.create(
            user=self.user,
            item_type="text",
            provider="manual",
            content_text="placeholder",
        )

    def _attach_triage(self, item: IngestItem, route: str):
        from src.intent_router.models import ItemTriageResult
        ItemTriageResult.objects.create(
            item=item,
            primary_route=route,
            confidence=0.95,
        )

    def test_triage_task_returns_true(self):
        item = self._create_item()
        self._attach_triage(item, "task")
        self.assertTrue(has_todo_classification(item))

    def test_projection_intent_todo_create(self):
        item = self._create_item()
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.task.create.todo",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )
        self.assertTrue(has_todo_classification(item))

    def test_projection_no_match_returns_false(self):
        item = self._create_item()
        ItemRetrievalProjection.objects.create(
            ingest_item=item,
            user=self.user,
            primary_intent_key="intent.capture.note.freeform",
            primary_subject_key="personal.daily.diary",
            primary_context_key="context.self.daily.routine",
            governance_key="gov.personal.private.self_only",
        )
        self.assertFalse(has_todo_classification(item))

    def test_no_triage_no_projection_returns_false(self):
        item = self._create_item()
        self.assertFalse(has_todo_classification(item))
