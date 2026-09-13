"""
Tests for the journal/instruction session-mode rule.

The cue: the recorder caps one recording at 240s and auto-continues with the SAME
session id, so a session containing a cap-length tranche is a long "journal" talk.
"""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem
from src.ingestion.session_mode import (
    INSTRUCTION,
    JOURNAL,
    clip_is_cap,
    resolve_session_mode,
)


class SessionModeTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="session-mode@example.com", password="***"
        )

    def _clip(self, duration, minutes_ago=0, group_id=None, deleted=False, item_type="audio",
              audio_duration=None):
        return IngestItem.objects.create(
            user=self.user,
            item_type=item_type,
            status="processed",
            is_deleted=deleted,
            occurred_at=timezone.now() - timedelta(minutes=minutes_ago),
            content_text="some text",
            recording_duration_seconds=duration,
            audio_duration_seconds=audio_duration,
            recording_group_id=group_id,
        )

    # ------------------------------------------------------------ the cap test

    def test_cap_test_never_uses_equality(self):
        self.assertTrue(clip_is_cap(self._clip(240)))
        self.assertTrue(clip_is_cap(self._clip(335)))  # real overrun in live data
        self.assertFalse(clip_is_cap(self._clip(239)))
        self.assertFalse(clip_is_cap(self._clip(157)))

    def test_cap_test_falls_back_to_processed_duration_with_tolerance(self):
        self.assertTrue(clip_is_cap(self._clip(None, audio_duration=239.6)))
        self.assertFalse(clip_is_cap(self._clip(None, audio_duration=239.0)))

    # ---------------------------------------------------------------- the rule

    def test_single_subcap_clip_is_instruction(self):
        clip = self._clip(55, group_id=uuid.uuid4())
        self.assertEqual(resolve_session_mode(self.user.pk, clip), INSTRUCTION)

    def test_single_cap_clip_is_journal(self):
        clip = self._clip(240, group_id=uuid.uuid4())
        self.assertEqual(resolve_session_mode(self.user.pk, clip), JOURNAL)

    def test_tail_of_a_capped_session_is_journal(self):
        """THE critical case: a sub-cap clip in a session holding a cap tranche."""
        gid = uuid.uuid4()
        self._clip(240, minutes_ago=5, group_id=gid)
        tail = self._clip(16, minutes_ago=1, group_id=gid)

        self.assertEqual(resolve_session_mode(self.user.pk, tail), JOURNAL)

    def test_subcap_only_session_is_instruction(self):
        gid = uuid.uuid4()
        self._clip(30, minutes_ago=5, group_id=gid)
        clip = self._clip(20, minutes_ago=1, group_id=gid)

        self.assertEqual(resolve_session_mode(self.user.pk, clip), INSTRUCTION)

    def test_deleted_cap_tranche_does_not_count(self):
        gid = uuid.uuid4()
        self._clip(240, minutes_ago=5, group_id=gid, deleted=True)
        clip = self._clip(16, minutes_ago=1, group_id=gid)

        self.assertEqual(resolve_session_mode(self.user.pk, clip), INSTRUCTION)

    def test_other_users_do_not_influence_the_mode(self):
        gid = uuid.uuid4()
        other = CustomUser.objects.create_user(email="other-mode@example.com", password="***")
        IngestItem.objects.create(
            user=other, item_type="audio", status="processed", is_deleted=False,
            occurred_at=timezone.now() - timedelta(minutes=5),
            content_text="their long talk", recording_duration_seconds=240,
            recording_group_id=gid,
        )
        clip = self._clip(16, minutes_ago=1, group_id=gid)

        self.assertEqual(resolve_session_mode(self.user.pk, clip), INSTRUCTION)

    # ------------------------------------------- legacy clips (no session id)

    def test_legacy_subcap_next_to_a_cap_clip_is_journal(self):
        self._clip(240, minutes_ago=5)  # no group id
        clip = self._clip(16, minutes_ago=1)

        self.assertEqual(resolve_session_mode(self.user.pk, clip), JOURNAL)

    def test_legacy_subcap_far_from_any_cap_is_instruction(self):
        self._clip(240, minutes_ago=60)  # outside the conversation gap window
        clip = self._clip(16, minutes_ago=0)

        self.assertEqual(resolve_session_mode(self.user.pk, clip), INSTRUCTION)

    def test_legacy_cap_clip_is_journal(self):
        clip = self._clip(240)
        self.assertEqual(resolve_session_mode(self.user.pk, clip), JOURNAL)

    # ------------------------------------------------------- non-audio inputs

    def test_text_entry_is_instruction(self):
        clip = self._clip(None, item_type="text")
        self.assertEqual(resolve_session_mode(self.user.pk, clip), INSTRUCTION)
