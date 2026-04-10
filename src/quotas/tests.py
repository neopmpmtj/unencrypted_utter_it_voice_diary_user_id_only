"""
Tests for token-based quota enforcement and is_app_admin bypass behavior.
"""

from datetime import timedelta

from django.test import TestCase, Client
from django.utils import timezone

from src.accounts.models import APIUsageLog, CustomUser, UserPreferences
from src.ingestion.models import IngestItem, ItemType
from src.common.utils.rate_limiter import check_transcription_rate_limit
from src.quotas.services import (
    check_token_quota,
    can_use_feature,
    get_today_token_sum,
    get_user_quota_summary,
)


class AppAdminUserQuotaBypassTests(TestCase):
    """Tests that app admin users bypass all quotas and get show_usage_card=False."""

    def setUp(self):
        self.app_admin = CustomUser.objects.create_user(
            email="appadmin@example.com",
            password="Pass123",
        )
        self.app_admin.is_email_verified = True
        self.app_admin.is_app_admin = True
        self.app_admin.tier = "free"
        self.app_admin.save()

    def test_app_admin_user_has_unlimited_token_quota(self):
        allowed, remaining, info = check_token_quota(self.app_admin)
        self.assertTrue(allowed)
        self.assertEqual(info["is_app_admin"], True)
        self.assertEqual(info["limit_tokens"], 0)

    def test_app_admin_user_can_use_all_features(self):
        self.assertTrue(can_use_feature(self.app_admin, "edit"))

    def test_app_admin_user_quota_summary_show_usage_card_false(self):
        summary = get_user_quota_summary(self.app_admin)
        self.assertFalse(summary["show_usage_card"])
        self.assertTrue(summary["is_app_admin"])

    def test_app_admin_user_transcription_rate_limit_bypassed(self):
        allowed, info = check_transcription_rate_limit(self.app_admin)
        self.assertTrue(allowed)


class AppAdminUserQuotaAPITest(TestCase):
    """Test that GET /voice/quota/ returns show_usage_card: false for app admin."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.app_admin = CustomUser.objects.create_user(
            email="apiadmin@example.com",
            password="Pass123",
        )
        self.app_admin.is_email_verified = True
        self.app_admin.is_app_admin = True
        self.app_admin.save()
        self.client.force_login(self.app_admin)
        prefs = UserPreferences.objects.get(user=self.app_admin)
        prefs.onboarding_completed = True
        prefs.save()

    def test_quota_endpoint_returns_show_usage_card_false_for_app_admin(self):
        response = self.client.get("/voice/quota/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["show_usage_card"])
        self.assertTrue(data["is_app_admin"])
        self.assertIn("tokens", data)


class DashboardQuotaEndpointTests(TestCase):
    """Tests for GET /voice/quota/dashboard/ (lightweight tier-only endpoint)."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = CustomUser.objects.create_user(
            email="dashboard@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "free"
        self.user.save()
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_dashboard_endpoint_returns_tier_and_tokens(self):
        response = self.client.get("/voice/quota/dashboard/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("tier", data)
        self.assertIn("show_usage_card", data)
        self.assertIn("tokens_used", data)
        self.assertIn("tokens_limit", data)
        self.assertTrue(data["show_usage_card"])

    def test_dashboard_endpoint_app_admin_show_usage_card_false(self):
        self.user.is_app_admin = True
        self.user.save()
        response = self.client.get("/voice/quota/dashboard/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["show_usage_card"])


class TokenQuotaEnforcementTests(TestCase):
    """Tests that token quota is enforced based on APIUsageLog."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="tokentest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "free"
        self.user.save()
        UserPreferences.objects.get_or_create(user=self.user)

    def test_token_quota_allows_when_under_limit(self):
        allowed, remaining, info = check_token_quota(self.user)
        self.assertTrue(allowed)
        self.assertGreater(info["limit_tokens"], 0)
        self.assertEqual(info["used_tokens"], 0)

    def test_token_quota_blocks_when_over_limit(self):
        APIUsageLog.objects.create(
            user=self.user,
            service="test",
            usage_type="input_tokens",
            amount=40_000,
        )
        APIUsageLog.objects.create(
            user=self.user,
            service="test",
            usage_type="output_tokens",
            amount=15_000,
        )
        allowed, remaining, info = check_token_quota(self.user)
        self.assertFalse(allowed)
        self.assertEqual(info["used_tokens"], 55_000)
        self.assertGreaterEqual(info["limit_tokens"], 50_000)
        self.assertEqual(info["remaining_tokens"], 0)

    def test_get_today_token_sum_sums_input_and_output_only(self):
        tz = timezone.get_current_timezone()
        today_start = timezone.now().astimezone(tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        APIUsageLog.objects.create(
            user=self.user,
            service="test",
            usage_type="input_tokens",
            amount=100,
        )
        APIUsageLog.objects.create(
            user=self.user,
            service="test",
            usage_type="output_tokens",
            amount=50,
        )
        APIUsageLog.objects.create(
            user=self.user,
            service="test",
            usage_type="audio_minutes",
            amount=5,
        )
        total = get_today_token_sum(self.user)
        self.assertEqual(total, 150)


class UsageStatsApiScopeTests(TestCase):
    """Tests that usage_stats_api scope filters (all, today, week, month) produce correct date ranges."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = CustomUser.objects.create_user(
            email="scopetest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.tier = "free"
        self.user.save()
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def _create_entry(self, item_type, occurred_at=None, ingested_at=None):
        kwargs = {
            "user": self.user,
            "item_type": item_type,
            "is_deleted": False,
        }
        if occurred_at is not None:
            kwargs["occurred_at"] = occurred_at
        item = IngestItem.objects.create(**kwargs)
        if ingested_at is not None:
            IngestItem.objects.filter(pk=item.pk).update(ingested_at=ingested_at)
        return item

    def test_scope_all_returns_all_entries(self):
        now = timezone.now()
        self._create_entry(ItemType.AUDIO, occurred_at=now)
        self._create_entry(ItemType.TEXT, occurred_at=now - timedelta(days=40))

        response = self.client.get(
            "/voice/usage/api/?scope=all",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total_entries"], 2)
        self.assertEqual(data["stats"]["audio_entries"], 1)
        self.assertEqual(data["stats"]["text_entries"], 1)
        self.assertIn("tokens", data["quota"])

    def test_scope_today_filters_to_midnight_today(self):
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._create_entry(ItemType.AUDIO, occurred_at=now)
        self._create_entry(ItemType.TEXT, occurred_at=today_start)
        self._create_entry(ItemType.AUDIO, occurred_at=now - timedelta(days=1))

        response = self.client.get(
            "/voice/usage/api/?scope=today",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total_entries"], 2)
        self.assertEqual(data["stats"]["audio_entries"], 1)
        self.assertEqual(data["stats"]["text_entries"], 1)

    def test_scope_today_includes_null_occurred_at_using_ingested_at(self):
        now = timezone.now()
        item = self._create_entry(ItemType.TEXT, occurred_at=None)
        IngestItem.objects.filter(pk=item.pk).update(ingested_at=now)

        response = self.client.get(
            "/voice/usage/api/?scope=today",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total_entries"], 1)

    def test_scope_week_filters_to_last_7_days(self):
        now = timezone.now()
        self._create_entry(ItemType.AUDIO, occurred_at=now)
        self._create_entry(ItemType.TEXT, occurred_at=now - timedelta(days=3))
        self._create_entry(ItemType.AUDIO, occurred_at=now - timedelta(days=10))

        response = self.client.get(
            "/voice/usage/api/?scope=week",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total_entries"], 2)
        self.assertEqual(data["stats"]["audio_entries"], 1)
        self.assertEqual(data["stats"]["text_entries"], 1)

    def test_scope_month_filters_to_last_30_days(self):
        now = timezone.now()
        self._create_entry(ItemType.AUDIO, occurred_at=now)
        self._create_entry(ItemType.TEXT, occurred_at=now - timedelta(days=15))
        self._create_entry(ItemType.AUDIO, occurred_at=now - timedelta(days=40))

        response = self.client.get(
            "/voice/usage/api/?scope=month",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total_entries"], 2)
        self.assertEqual(data["stats"]["audio_entries"], 1)
        self.assertEqual(data["stats"]["text_entries"], 1)

    def test_scope_default_is_all(self):
        now = timezone.now()
        self._create_entry(ItemType.AUDIO, occurred_at=now - timedelta(days=50))

        response = self.client.get(
            "/voice/usage/api/",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total_entries"], 1)

    def test_scope_invalid_falls_back_to_all(self):
        now = timezone.now()
        self._create_entry(ItemType.TEXT, occurred_at=now - timedelta(days=50))

        response = self.client.get(
            "/voice/usage/api/?scope=invalid",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total_entries"], 1)
