"""
Tests for financial_parser views.

- Item DELETE: soft-delete item; soft-delete record when no items remain
"""

from decimal import Decimal

from django.test import RequestFactory, TestCase

from src.accounts.models import CustomUser
from src.financial_parser.models import FinancialItem, FinancialRecord, FinancialRecordStatus
from src.financial_parser.views import financial_item_api


class FinancialItemDeleteViewTests(TestCase):
    """Tests for financial item DELETE API (soft-delete and record cascade)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = CustomUser.objects.create_user(
            email="finviewdel@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_delete_last_item_soft_deletes_record(self):
        record = FinancialRecord.objects.create(
            user=self.user,
            created_by=self.user,
            record_name="test",
            status=FinancialRecordStatus.SUCCESS,
        )
        fi = FinancialItem.objects.create(
            financial_record=record,
            item_index=0,
            type="expense",
            amount=Decimal("10"),
            currency="EUR",
        )

        request = self.factory.delete(f"/api/financials/{fi.id}/item/")
        request.user = self.user

        response = financial_item_api(request, item_id=str(fi.id))

        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertEqual(FinancialItem.objects.filter(financial_record=record).count(), 0)
        self.assertEqual(FinancialItem.all_objects.filter(financial_record=record).count(), 1)
        fi_refreshed = FinancialItem.all_objects.get(id=fi.id)
        self.assertIsNotNone(fi_refreshed.deleted_at)
