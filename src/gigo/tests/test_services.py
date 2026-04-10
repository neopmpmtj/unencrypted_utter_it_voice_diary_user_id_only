"""Tests for GIGO services."""

from django.test import TestCase

from src.accounts.models import CustomUser, UserPreferences
from src.ingestion.models import IngestItem, IngestStatus

from src.gigo.models import GigoEntry, GigoNudgeLog, GigoUserState, GigoRank
from src.gigo.services import compute_rank, record_entry, get_alert_pending, dismiss_alert


class ComputeRankTests(TestCase):
    """Tests for compute_rank."""

    def test_low_rank(self):
        for n in [0, 1, 5, 7]:
            self.assertEqual(compute_rank(n), GigoRank.LOW)

    def test_medium_rank(self):
        for n in [8, 10, 15]:
            self.assertEqual(compute_rank(n), GigoRank.MEDIUM)

    def test_high_rank(self):
        for n in [16, 20, 100]:
            self.assertEqual(compute_rank(n), GigoRank.HIGH)


class RecordEntryTests(TestCase):
    """Tests for record_entry."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="gigo@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_record_entry_creates_gigo_entry(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.PROCESSED,
        )
        record_entry(
            user=self.user,
            item=item,
            content_text="one two three four five",
            item_type="text",
        )
        entry = GigoEntry.objects.get(user=self.user)
        self.assertEqual(entry.word_count, 5)
        self.assertEqual(entry.rank, GigoRank.LOW)
        self.assertEqual(entry.item_type, "text")

    def test_three_low_ranks_triggers_alert_and_nudge(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.PROCESSED,
        )
        for _ in range(3):
            record_entry(
                user=self.user,
                item=item,
                content_text="short",
                item_type="text",
            )
        state = GigoUserState.objects.get(user=self.user)
        self.assertEqual(state.consecutive_low_count, 3)
        self.assertTrue(state.alert_pending)
        self.assertEqual(GigoNudgeLog.objects.filter(user=self.user).count(), 1)

    def test_medium_rank_resets_counter(self):
        item = IngestItem.objects.create(
            user=self.user,
            item_type="text",
            status=IngestStatus.PROCESSED,
        )
        record_entry(
            user=self.user,
            item=item,
            content_text="one",
            item_type="text",
        )
        record_entry(
            user=self.user,
            item=item,
            content_text="one two",
            item_type="text",
        )
        record_entry(
            user=self.user,
            item=item,
            content_text="one two three four five six seven eight nine ten",
            item_type="text",
        )
        state = GigoUserState.objects.get(user=self.user)
        self.assertEqual(state.consecutive_low_count, 0)

    def test_record_entry_skips_when_no_user(self):
        record_entry(
            user=None,
            item=None,
            content_text="hello world",
            item_type="text",
        )
        self.assertEqual(GigoEntry.objects.count(), 0)


class GetAlertPendingTests(TestCase):
    """Tests for get_alert_pending."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="gigo@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        UserPreferences.objects.get(user=self.user)

    def test_returns_false_when_no_state(self):
        self.assertFalse(get_alert_pending(self.user))

    def test_returns_true_when_alert_pending(self):
        GigoUserState.objects.create(
            user=self.user,
            consecutive_low_count=3,
            alert_pending=True,
        )
        self.assertTrue(get_alert_pending(self.user))

    def test_returns_false_when_alert_not_pending(self):
        GigoUserState.objects.create(
            user=self.user,
            consecutive_low_count=0,
            alert_pending=False,
        )
        self.assertFalse(get_alert_pending(self.user))


class DismissAlertTests(TestCase):
    """Tests for dismiss_alert."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="gigo@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()
        UserPreferences.objects.get(user=self.user)

    def test_clears_alert_pending(self):
        GigoUserState.objects.create(
            user=self.user,
            consecutive_low_count=3,
            alert_pending=True,
        )
        dismiss_alert(self.user)
        state = GigoUserState.objects.get(user=self.user)
        self.assertFalse(state.alert_pending)
