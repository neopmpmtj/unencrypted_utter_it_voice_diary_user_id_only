"""
Logic-only tests for text_rewrite API views.
"""

import json
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences


class RewriteEntryApiTests(TestCase):
    """Tests for POST /api/entries/rewrite/."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.free_user = CustomUser.objects.create_user(
            email="free@example.com",
            password="Pass123",
        )
        self.free_user.is_email_verified = True
        self.free_user.tier = "free"
        self.free_user.save()

        self.pro_user = CustomUser.objects.create_user(
            email="pro@example.com",
            password="Pass123",
        )
        self.pro_user.is_email_verified = True
        self.pro_user.tier = "pro"
        self.pro_user.save()

        for u in (self.free_user, self.pro_user):
            prefs = UserPreferences.objects.get(user=u)
            prefs.onboarding_completed = True
            prefs.save()

        self.url = reverse("text_rewrite:api_rewrite")

    def test_rewrite_api_429_when_token_quota_exceeded(self):
        """Free user with token quota exhausted gets 429."""
        from src.accounts.models import APIUsageLog

        self.client.force_login(self.free_user)
        APIUsageLog.objects.create(
            user=self.free_user,
            service="test",
            usage_type="input_tokens",
            amount=40_000,
        )
        APIUsageLog.objects.create(
            user=self.free_user,
            service="test",
            usage_type="output_tokens",
            amount=15_000,
        )

        response = self.client.post(
            self.url,
            data=json.dumps({"text": "Hello", "template": "grammar"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data.get("error"), "quota_exceeded")

    def test_rewrite_api_302_when_not_logged_in(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "Hello", "template": "grammar"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (302, 401))

    def test_rewrite_api_400_for_invalid_json(self):
        self.client.force_login(self.free_user)
        response = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_rewrite_api_400_for_empty_text(self):
        self.client.force_login(self.free_user)
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "", "template": "grammar"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_rewrite_api_400_for_whitespace_only_text(self):
        self.client.force_login(self.free_user)
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "   \n  ", "template": "grammar"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_rewrite_api_400_for_invalid_template(self):
        self.client.force_login(self.free_user)
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "Hello", "template": "invalid_template"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("invalid_template", data["error"])

    @patch("src.text_rewrite.views.rewrite_text")
    def test_rewrite_api_200_with_correct_structure(self, mock_rewrite):
        mock_rewrite.return_value = ("Polished result", {"input": 5, "output": 3, "total": 8})

        self.client.force_login(self.free_user)
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "Hello world", "template": "grammar"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["rewritten_text"], "Polished result")
        self.assertEqual(data["template_used"], "grammar")
        self.assertEqual(data["tokens"]["input"], 5)
        self.assertEqual(data["tokens"]["output"], 3)
        self.assertEqual(data["tokens"]["total"], 8)

        mock_rewrite.assert_called_once()
        call_args = mock_rewrite.call_args
        self.assertEqual(call_args[0][0], "Hello world")
        self.assertEqual(call_args[0][1], "grammar")

    @patch("src.text_rewrite.views.rewrite_text")
    def test_rewrite_api_uses_default_template_when_omitted(self, mock_rewrite):
        mock_rewrite.return_value = ("Result", {"input": 1, "output": 1, "total": 2})

        self.client.force_login(self.free_user)
        response = self.client.post(
            self.url,
            data=json.dumps({"text": "Hello"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        call_args = mock_rewrite.call_args
        self.assertEqual(call_args[0][0], "Hello")
        self.assertEqual(call_args[0][1], "grammar")
