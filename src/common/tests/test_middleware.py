"""
Tests for NoCacheAuthenticatedMiddleware.
"""

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from src.accounts.models import CustomUser, UserPreferences
from src.common.middleware import NoCacheAuthenticatedMiddleware


def _get_response(request):
    return HttpResponse("<html></html>", content_type="text/html")


class NoCacheAuthenticatedMiddlewareTests(TestCase):
    """Tests that middleware adds no-cache headers for authenticated HTML responses."""

    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.user = CustomUser.objects.create_user(
            email="nocache@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

        prefs = UserPreferences.objects.get(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_authenticated_html_response_gets_no_cache_headers(self):
        """Authenticated user requesting HTML page receives Cache-Control, Pragma, Expires."""
        self.client.force_login(self.user)
        url = reverse("entries:list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("Content-Type", ""))
        self.assertEqual(
            response["Cache-Control"],
            "no-store, no-cache, must-revalidate, max-age=0",
        )
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["Expires"], "0")

    def test_unauthenticated_html_response_no_headers_added(self):
        """Middleware does not add no-cache headers when user is not authenticated."""
        request = self.factory.get("/entries/")
        request.user = AnonymousUser()
        middleware = NoCacheAuthenticatedMiddleware(_get_response)
        response = middleware(request)

        self.assertNotIn("Cache-Control", response)
        self.assertNotIn("Pragma", response)
        self.assertNotIn("Expires", response)

    def test_authenticated_json_response_no_cache_headers(self):
        """Authenticated API (JSON) response does not get no-cache headers."""
        self.client.force_login(self.user)
        url = reverse("entries:api_list")
        response = self.client.get(url, HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers.get("Content-Type", ""))
        self.assertNotIn("Cache-Control", response)
