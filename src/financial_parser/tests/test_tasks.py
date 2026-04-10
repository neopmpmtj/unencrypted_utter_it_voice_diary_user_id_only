"""
Tests for financial_parser tasks.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import IngestItem, IngestStatus

from src.financial_parser.models import FinancialRecord, FinancialRecordStatus
from src.financial_parser.tasks import parse_financial_task


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ParseFinancialTaskSavesContentTextTests(TestCase):
    """Tests for parse_financial_task."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="fintask@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "free"
        self.user.save()

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

        self.item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.TAGGED,
            occurred_at=timezone.now(),
            content_text="gastei 20 no café",
            summary_text="",
            title="",
        )

    @patch("src.financial_parser.services.extract_financial_items")
    @patch("src.financial_parser.tasks.broadcast_financial_status")
    @patch("src.financial_parser.tasks.broadcast_complete")
    @patch("src.financial_parser.tasks.get_channel_layer", return_value=None)
    def test_financial_record_stored_before_content_text_update(
        self, _mock_channel, _mock_bc, _mock_bfs, mock_extract,
    ):
        """FinancialRecord is persisted and marked success."""
        mock_extract.return_value = (
            "Despesas",
            "",
            [
                {"type": "expense", "amount": 10, "currency": "EUR",
                 "category": "Transport", "description": "uber"},
            ],
            None,
            {},
        )

        result = parse_financial_task(str(self.item.id), "", "pt")

        self.assertTrue(result["success"])
        record = FinancialRecord.objects.get(id=result["financial_record_id"])
        self.assertEqual(record.status, FinancialRecordStatus.SUCCESS)
        self.assertEqual(record.items.count(), 1)
