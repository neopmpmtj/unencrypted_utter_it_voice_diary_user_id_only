"""
Regression tests for the per-user "Summarize long conversations" preference.

WHY THESE EXIST
---------------
The summarizer tests in ``conversation_summarizer`` set
``enable_conversation_summary = True`` explicitly in ``setUp``, so a broken
DEFAULT — or a settings page that renders/saves the toggle wrongly — would sail
straight through them. These tests cover the user-facing contract instead:

* a new user's preference is ON,
* the settings page renders the toggle checked,
* saving the settings page preserves it (and the other flags),
* a save that omits the field (e.g. a stale page) turns it off — the hazard this
  guards against, documented as a test so the behaviour is explicit.
"""

import re

from django.test import TestCase
from django.urls import reverse

from src.accounts.forms import UserPreferencesForm
from src.accounts.models import CustomUser, UserPreferences

TOGGLE = "enable_conversation_summary"
OTHER_BOOLEAN_FLAGS = (
    "enable_translation",
    "show_inline_rewrite",
    "show_recording_timer",
    "standalone_app_ui",
)


class SummarizerPreferenceDefaultTests(TestCase):
    def test_new_user_preferences_default_to_summaries_on(self):
        """The model default must be ON — the feature ships enabled."""
        user = CustomUser.objects.create_user(email="prefs-default@example.com", password="***")
        prefs = UserPreferences.objects.get(user=user)
        self.assertTrue(
            prefs.enable_conversation_summary,
            "a brand-new user must have conversation summaries enabled by default",
        )

    def test_the_field_exists_on_the_preferences_form(self):
        self.assertIn(TOGGLE, UserPreferencesForm().fields)


class PreferencesFormIntegrityTests(TestCase):
    """
    Guards the bug that shipped the summarizer toggle broken.

    A field DECLARED on the form but missing from ``Meta.fields`` still renders,
    but the ModelForm never fills its initial from the instance — so
    ``{{ field.value }}`` is None, a checkbox renders unchecked even when the
    stored value is True, and saving silently persists the OFF state. That is
    exactly what happened to ``enable_conversation_summary``.
    """

    def test_every_form_boolean_field_gets_its_initial_from_the_instance(self):
        from django import forms as django_forms

        prefs = UserPreferences(
            enable_conversation_summary=True,
            enable_translation=True,
            show_inline_rewrite=True,
            show_recording_timer=True,
            standalone_app_ui=True,
        )
        form = UserPreferencesForm(instance=prefs)

        checked_fields = [
            name for name, field in form.fields.items()
            if isinstance(field, django_forms.BooleanField)
        ]
        self.assertIn(TOGGLE, checked_fields, "the summarizer toggle vanished from the form")

        for name in checked_fields:
            self.assertIn(
                name, form.initial,
                f"{name} is declared on UserPreferencesForm but missing from "
                "Meta.fields, so its checkbox can never render as checked",
            )
            self.assertTrue(
                form[name].value(),
                f"{name} should render checked for an instance whose value is True",
            )


class SummarizerPreferenceProfilePageTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="prefs-page@example.com", password="***"
        )
        self.client.force_login(self.user)
        self.prefs = UserPreferences.objects.get(user=self.user)
        # Otherwise the onboarding middleware redirects (302) and we never render
        # the settings page at all — a trap that made an earlier version of these
        # tests look like "the toggle is missing".
        self.prefs.onboarding_completed = True
        self.prefs.save(update_fields=["onboarding_completed"])
        self.url = reverse("accounts:profile")

    def _page(self):
        """
        Fetch the settings page, refusing to interpret anything but a real render.

        Without this guard a 400/500 error page looks exactly like "the toggle is
        missing" — which is precisely how a test can lie.
        """
        response = self.client.get(self.url)
        self.assertEqual(
            response.status_code, 200,
            f"settings page did not render (status {response.status_code})",
        )
        return response.content.decode()

    def _input_tag(self, html, name):
        match = re.search(r'<input[^>]*name="%s"[^>]*>' % re.escape(name), html)
        self.assertIsNotNone(match, f'no input named "{name}" on the settings page')
        return match.group(0)

    def _payload(self, **overrides):
        """A browsable settings POST, using the instance's own valid values."""
        data = {
            "save_preferences": "1",
            "preferred_language": self.prefs.preferred_language,
            "timezone": self.prefs.timezone,
            "drive_attachment_folder_name": self.prefs.drive_attachment_folder_name or "",
        }
        data.update(overrides)
        return data

    def test_settings_page_renders_the_toggle_checked_for_a_new_user(self):
        html = self._page()

        self.assertIn("checked", self._input_tag(html, TOGGLE))

    def test_settings_page_reflects_a_disabled_preference(self):
        UserPreferences.objects.filter(user=self.prefs.user).update(enable_conversation_summary=False)

        html = self._page()

        self.assertNotIn("checked", self._input_tag(html, TOGGLE))

    def test_saving_settings_keeps_summaries_on(self):
        response = self.client.post(self.url, self._payload(**{TOGGLE: "on"}))

        self.assertEqual(response.status_code, 302)
        self.prefs.refresh_from_db()
        self.assertTrue(self.prefs.enable_conversation_summary)

    def test_saving_settings_preserves_every_other_flag(self):
        """Saving the settings page must not silently disable unrelated toggles."""
        for flag in OTHER_BOOLEAN_FLAGS:
            self.assertTrue(getattr(self.prefs, flag, True), f"{flag} should start on for this test")

        payload = self._payload(**{TOGGLE: "on"}, **{flag: "on" for flag in OTHER_BOOLEAN_FLAGS})
        self.client.post(self.url, payload)

        self.prefs.refresh_from_db()
        for flag in OTHER_BOOLEAN_FLAGS:
            self.assertTrue(getattr(self.prefs, flag), f"saving settings silently disabled {flag}")

    def test_a_save_that_omits_the_field_turns_it_off(self):
        """
        Documents the hazard: an unchecked/absent checkbox means False in HTML.

        This is why the settings page must never be submitted from a stale render
        (one carrying an older set of fields) — the missing field would opt the
        user out silently.
        """
        self.client.post(self.url, self._payload(**{TOGGLE: "on"}))
        self.prefs.refresh_from_db()
        self.assertTrue(self.prefs.enable_conversation_summary)

        self.client.post(self.url, self._payload())  # same page, toggle not sent

        self.prefs.refresh_from_db()
        self.assertFalse(self.prefs.enable_conversation_summary)
