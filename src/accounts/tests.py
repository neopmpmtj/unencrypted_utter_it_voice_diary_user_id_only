import time
from unittest.mock import patch

from django.conf import settings
from django.contrib import auth
from django.core.cache import cache
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from src.common.utils.rate_limiter import IdentifierRateLimiter

from .models import CustomUser, GlobalSettings, UserPreferences
from .audio_retention_config import get_audio_retention_days, get_audio_retention_hours


class GetAudioRetentionHoursTests(TestCase):
    """Tests for get_audio_retention_hours."""

    def test_returns_default_when_no_globalsetting(self):
        """Returns 1 when storage.audio_retention_hours is not set."""
        self.assertEqual(get_audio_retention_hours(), 1)

    def test_returns_value_from_globalsettings(self):
        """Returns value from GlobalSettings when set."""
        GlobalSettings.objects.create(
            key='storage.audio_retention_hours',
            value=2,
        )
        self.assertEqual(get_audio_retention_hours(), 2)

    def test_returns_int_even_if_stored_as_string(self):
        """Converts string value to int."""
        GlobalSettings.objects.create(
            key='storage.audio_retention_hours',
            value='3',
        )
        self.assertEqual(get_audio_retention_hours(), 3)


class GetAudioRetentionDaysTests(TestCase):
    """Tests for get_audio_retention_days."""

    def test_returns_default_when_no_globalsetting(self):
        """Returns 3 when storage.audio_retention_days is not set."""
        self.assertEqual(get_audio_retention_days(), 3)

    def test_returns_value_from_globalsettings(self):
        """Returns value from GlobalSettings when set."""
        GlobalSettings.objects.create(
            key='storage.audio_retention_days',
            value=7,
        )
        self.assertEqual(get_audio_retention_days(), 7)

    def test_returns_int_even_if_stored_as_string(self):
        """Converts string value to int."""
        GlobalSettings.objects.create(
            key='storage.audio_retention_days',
            value='5',
        )
        self.assertEqual(get_audio_retention_days(), 5)


class IdentifierRateLimiterTests(TestCase):
    """Unit tests for IdentifierRateLimiter (unique prefix per test to avoid cache collisions)."""

    def setUp(self):
        cache.clear()
        self.prefix = f"test_idlim_{self.id()}"
        self.limiter = IdentifierRateLimiter(
            cache_key_prefix=self.prefix,
            max_requests=2,
            window_seconds=60,
        )

    def test_first_request_allowed(self):
        allowed, info = self.limiter.check_rate_limit("id1")
        self.assertTrue(allowed)
        self.assertEqual(info["requests_made"], 1)
        self.assertEqual(info["requests_remaining"], 1)
        self.assertEqual(info["retry_after_seconds"], 0)

    def test_requests_up_to_max_allowed(self):
        allowed1, _ = self.limiter.check_rate_limit("id1")
        allowed2, info2 = self.limiter.check_rate_limit("id1")
        self.assertTrue(allowed1)
        self.assertTrue(allowed2)
        self.assertEqual(info2["requests_made"], 2)
        self.assertEqual(info2["requests_remaining"], 0)

    def test_next_request_after_max_denied(self):
        self.limiter.check_rate_limit("id1")
        self.limiter.check_rate_limit("id1")
        allowed, info = self.limiter.check_rate_limit("id1")
        self.assertFalse(allowed)
        self.assertGreater(info["retry_after_seconds"], 0)
        self.assertEqual(info["requests_remaining"], 0)

    def test_window_expiry_allows_again(self):
        short_limiter = IdentifierRateLimiter(
            cache_key_prefix=f"{self.prefix}_short",
            max_requests=1,
            window_seconds=1,
        )
        short_limiter.check_rate_limit("id1")
        allowed_immediate, _ = short_limiter.check_rate_limit("id1")
        self.assertFalse(allowed_immediate)
        time.sleep(1.1)
        allowed_after, _ = short_limiter.check_rate_limit("id1")
        self.assertTrue(allowed_after)

    def test_reset_limit_clears_state(self):
        self.limiter.check_rate_limit("id1")
        self.limiter.check_rate_limit("id1")
        self.limiter.reset_limit("id1")
        allowed, info = self.limiter.check_rate_limit("id1")
        self.assertTrue(allowed)
        self.assertEqual(info["requests_made"], 1)

    def test_cache_failure_fails_open(self):
        """Cache failure returns (True, {}) so request is allowed."""
        with patch('src.common.utils.rate_limiter.cache') as mock_cache:
            mock_cache.get.side_effect = ConnectionError("Redis down")
            allowed, info = self.limiter.check_rate_limit("id1")
        self.assertTrue(allowed)
        self.assertEqual(info, {})


class LoginRateLimitViewTests(TestCase):
    """Integration tests for login view rate limiting -> 429."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse('accounts:login')
        self.login_attempt_limiter = __import__(
            'src.common.utils.rate_limiter', fromlist=['login_attempt_limiter']
        ).login_attempt_limiter

    def test_below_limit_returns_200(self):
        """Invalid form below rate limit returns 200."""
        response = self.client.post(self.url, {'email': 'x', 'password': ''})
        self.assertEqual(response.status_code, 200)

    def test_above_limit_returns_429(self):
        """Exceed login rate limit returns 429."""
        invalid_data = {'email': '', 'password': ''}
        for _ in range(self.login_attempt_limiter.max_requests):
            self.client.post(self.url, invalid_data)
        response = self.client.post(self.url, invalid_data)
        self.assertEqual(response.status_code, 429)
        self.assertIn(b"Too many login attempts", response.content)

    def test_cache_failure_fails_open_returns_200(self):
        """Cache failure in rate limiter allows request (200)."""
        with patch('src.common.utils.rate_limiter.cache') as mock_cache:
            mock_cache.get.side_effect = ConnectionError("Redis down")
            response = self.client.post(self.url, {'email': 'x', 'password': ''})
        self.assertEqual(response.status_code, 200)


class CustomUserModelTests(TestCase):
    """Tests for CustomUser model"""
    
    def test_create_user(self):
        """Test creating a regular user"""
        user = CustomUser.objects.create_user(
            email='test@example.com',
            password='SecurePass123'
        )
        
        self.assertEqual(user.email, 'test@example.com')
        self.assertTrue(user.check_password('SecurePass123'))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
    
    def test_create_superuser(self):
        """Test creating a superuser"""
        admin = CustomUser.objects.create_superuser(
            email='admin@example.com',
            password='AdminPass123'
        )
        
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
    
    def test_unique_email(self):
        """Test that emails must be unique"""
        CustomUser.objects.create_user(
            email='test@example.com',
            password='Pass123'
        )
        
        with self.assertRaises(Exception):
            CustomUser.objects.create_user(
                email='test@example.com',
                password='Pass456'
            )


class UserPreferencesModelTests(TestCase):
    """Tests for UserPreferences model."""

    def test_show_recording_timer_defaults_to_true(self):
        """UserPreferences created via signal should have show_recording_timer=True by default."""
        user = CustomUser.objects.create_user(
            email='preftest@example.com',
            password='Pass123',
        )
        prefs = UserPreferences.objects.get(user=user)
        self.assertTrue(prefs.show_recording_timer)

    def test_transcription_text_size_defaults_to_small(self):
        """UserPreferences created via signal should have transcription_text_size='small' by default."""
        user = CustomUser.objects.create_user(
            email='sizetest@example.com',
            password='Pass123',
        )
        prefs = UserPreferences.objects.get(user=user)
        self.assertEqual(prefs.transcription_text_size, 'small')

    def test_interface_language_defaults_to_pt_pt(self):
        """UserPreferences created via signal should have interface_language='pt-pt' by default."""
        user = CustomUser.objects.create_user(
            email='langtest@example.com',
            password='Pass123',
        )
        prefs = UserPreferences.objects.get(user=user)
        self.assertEqual(prefs.interface_language, 'pt-pt')

    def test_timezone_field_default_is_lisbon(self):
        user = CustomUser.objects.create_user(
            email='tzdefault@example.com',
            password='Pass123',
        )
        prefs = UserPreferences.objects.get(user=user)
        self.assertEqual(prefs.timezone, 'Europe/Lisbon')

    def test_timezone_field_saves_paris(self):
        user = CustomUser.objects.create_user(
            email='tzparis@example.com',
            password='Pass123',
        )
        prefs = UserPreferences.objects.get(user=user)
        prefs.timezone = 'Europe/Paris'
        prefs.save()
        prefs.refresh_from_db()
        self.assertEqual(prefs.timezone, 'Europe/Paris')


class RegistrationTests(TestCase):
    """Tests for registration view"""
    
    def setUp(self):
        self.client = Client()
        self.register_url = reverse('accounts:register')
    
    def test_registration_page_loads(self):
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
    
    def test_user_registration(self):
        data = {
            'email': 'newuser@example.com',
            'password1': 'SecurePass123',
            'password2': 'SecurePass123',
        }
        
        response = self.client.post(self.register_url, data)
        
        # Check user was created
        self.assertTrue(
            CustomUser.objects.filter(email='newuser@example.com').exists()
        )
        
        # Check redirected to login
        self.assertRedirects(response, reverse('accounts:login'))
    
    def test_duplicate_email_rejected(self):
        # Create first user
        CustomUser.objects.create_user(
            email='existing@example.com',
            password='Pass123'
        )
        
        # Try to register with same email
        data = {
            'email': 'existing@example.com',
            'password1': 'NewPass123',
            'password2': 'NewPass123',
        }
        
        response = self.client.post(self.register_url, data)
        
        # Should not create user
        self.assertEqual(
            CustomUser.objects.filter(email='existing@example.com').count(),
            1
        )

class LoginTests(TestCase):
    """Tests for login view"""
    
    def setUp(self):
        self.client = Client()
        self.login_url = reverse('accounts:login')
        
        # Create test user (verified so login form accepts)
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='SecurePass123'
        )
        self.user.is_email_verified = True
        self.user.save()
    
    def test_login_page_loads(self):
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)
    
    def test_user_login(self):
        data = {
            'email': 'test@example.com',
            'password': 'SecurePass123',
        }
        
        response = self.client.post(self.login_url, data)
        
        self.assertEqual(response.status_code, 302)
        user = auth.get_user(self.client)
        self.assertTrue(user.is_authenticated)
        self.assertEqual(user, self.user)
    
    def test_wrong_password_fails(self):
        data = {
            'email': 'test@example.com',
            'password': 'WrongPassword',
        }
        
        response = self.client.post(self.login_url, data)
        
        self.assertFalse(auth.get_user(self.client).is_authenticated)


class VerifyEmailViewTests(TestCase):
    """Tests for VerifyEmailView."""

    def setUp(self):
        self.client = Client()

    def test_valid_token_verifies_user_and_redirects(self):
        user = CustomUser.objects.create_user(
            email='unverified@example.com',
            password='Pass123',
        )
        user.is_email_verified = False
        user.email_verification_token = 'valid-token-123'
        user.save()

        url = reverse('accounts:verify_email', args=['valid-token-123'])
        response = self.client.get(url)

        self.assertRedirects(response, reverse('accounts:login'))
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        self.assertIsNone(user.email_verification_token)

    def test_invalid_token_shows_error_and_redirects(self):
        user = CustomUser.objects.create_user(
            email='unverified@example.com',
            password='Pass123',
        )
        user.is_email_verified = False
        user.email_verification_token = 'real-token'
        user.save()

        url = reverse('accounts:verify_email', args=['wrong-token'])
        response = self.client.get(url)

        self.assertRedirects(response, reverse('accounts:login'))
        user.refresh_from_db()
        self.assertFalse(user.is_email_verified)
        self.assertEqual(user.email_verification_token, 'real-token')


class LoginUnverifiedTests(TestCase):
    """Login with unverified user must not authenticate and must show verification required."""

    def setUp(self):
        self.client = Client()
        self.login_url = reverse('accounts:login')
        self.user = CustomUser.objects.create_user(
            email='unverified@example.com',
            password='SecurePass123',
        )
        self.user.is_email_verified = False
        self.user.save()

    def test_unverified_user_cannot_login_shows_resend(self):
        response = self.client.post(self.login_url, {
            'email': 'unverified@example.com',
            'password': 'SecurePass123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(auth.get_user(self.client).is_authenticated)
        self.assertIn('show_resend_verification', response.context)
        self.assertTrue(response.context['show_resend_verification'])
        self.assertEqual(response.context.get('unverified_email'), 'unverified@example.com')


class ResendVerificationTests(TestCase):
    """Tests for resend verification view."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('accounts:resend_verification')

    @patch('src.accounts.views._send_verification_email')
    def test_resend_verification_sends_email_and_redirects(self, mock_send):
        user = CustomUser.objects.create_user(
            email='unverified@example.com',
            password='Pass123',
        )
        user.is_email_verified = False
        user.email_verification_token = 'old-token'
        user.save()

        response = self.client.post(self.url, {'email': 'unverified@example.com'})

        self.assertRedirects(response, reverse('accounts:login'))
        mock_send.assert_called_once()
        call_user = mock_send.call_args[0][1]
        self.assertEqual(call_user.email, 'unverified@example.com')
        user.refresh_from_db()
        self.assertIsNotNone(user.email_verification_token)
        self.assertNotEqual(user.email_verification_token, 'old-token')


class LoginEdgeCasesTests(TestCase):
    """Login: inactive user, Google-only user, redirect when already authenticated."""

    def setUp(self):
        self.client = Client()
        self.login_url = reverse('accounts:login')
        self.register_url = reverse('accounts:register')

    def test_inactive_user_cannot_login(self):
        user = CustomUser.objects.create_user(
            email='inactive@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.is_active = False
        user.save()

        response = self.client.post(self.login_url, {
            'email': 'inactive@example.com',
            'password': 'Pass123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(auth.get_user(self.client).is_authenticated)
        self.assertTrue(response.context['form'].errors)

    def test_google_only_user_shows_hint_not_authenticated(self):
        user = CustomUser.objects.create_user(
            email='googleonly@example.com',
            password=None,
        )
        user.set_unusable_password()
        user.is_google_account = True
        user.is_email_verified = True
        user.save()

        response = self.client.post(self.login_url, {
            'email': 'googleonly@example.com',
            'password': 'any-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(auth.get_user(self.client).is_authenticated)
        self.assertTrue(response.context.get('show_google_hint'))
        self.assertEqual(response.context.get('google_email'), 'googleonly@example.com')

    def test_register_get_redirects_when_authenticated(self):
        user = CustomUser.objects.create_user(
            email='auth@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        prefs, _ = UserPreferences.objects.get_or_create(user=user)
        prefs.onboarding_completed = True
        prefs.save()
        response = self.client.get(self.register_url)
        self.assertRedirects(response, reverse(settings.LOGIN_REDIRECT_URL))

    def test_login_get_redirects_when_authenticated(self):
        user = CustomUser.objects.create_user(
            email='auth@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        prefs, _ = UserPreferences.objects.get_or_create(user=user)
        prefs.onboarding_completed = True
        prefs.save()
        response = self.client.get(self.login_url)
        self.assertRedirects(response, reverse(settings.LOGIN_REDIRECT_URL))


class AccountDeletionTests(TestCase):
    """Tests for account deletion request, cancel, and done views."""

    def setUp(self):
        self.client = Client()
        self.delete_url = reverse('accounts:account_delete')
        self.done_url = reverse('accounts:account_delete_done')

    def _mark_onboarding_complete(self, user):
        prefs, _ = UserPreferences.objects.get_or_create(user=user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_request_get_shows_form_with_email_confirmation(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.get(self.delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
        self.assertIn('masked_email', response.context)

    def test_request_post_correct_email_schedules_deletion_and_logs_out(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.delete_url, {'confirmation_email': 'user@example.com'})
        self.assertRedirects(response, self.done_url)
        user.refresh_from_db()
        self.assertIsNotNone(user.deletion_requested_at)
        self.assertFalse(user.is_active)
        self.assertFalse(auth.get_user(self.client).is_authenticated)

    def test_request_post_wrong_email_returns_form_error(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.delete_url, {'confirmation_email': 'wrong@example.com'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        user.refresh_from_db()
        self.assertIsNone(user.deletion_requested_at)
        self.assertTrue(user.is_active)

    def test_request_get_when_already_requested_redirects_to_profile(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.deletion_requested_at = timezone.now()
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.get(self.delete_url)
        self.assertRedirects(response, reverse('accounts:profile'))

    def test_request_google_only_email_confirmation_schedules_deletion(self):
        user = CustomUser.objects.create_user(
            email='google@example.com',
            password=None,
        )
        user.set_unusable_password()
        user.is_google_account = True
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.get(self.delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
        response = self.client.post(self.delete_url, {'confirmation_email': 'google@example.com'})
        self.assertRedirects(response, self.done_url)
        user.refresh_from_db()
        self.assertIsNotNone(user.deletion_requested_at)
        self.assertFalse(user.is_active)
        self.assertFalse(auth.get_user(self.client).is_authenticated)

    def test_cancel_valid_token_restores_user(self):
        from django.core.signing import TimestampSigner
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.deletion_requested_at = timezone.now()
        user.is_active = False
        user.save()
        signer = TimestampSigner()
        token = signer.sign(str(user.pk))
        url = reverse('accounts:account_delete_cancel', args=[token])
        response = self.client.get(url)
        self.assertRedirects(response, reverse('accounts:login'))
        user.refresh_from_db()
        self.assertIsNone(user.deletion_requested_at)
        self.assertTrue(user.is_active)

    def test_cancel_invalid_token_redirects_with_error(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.deletion_requested_at = timezone.now()
        user.is_active = False
        user.save()
        url = reverse('accounts:account_delete_cancel', args=['invalid-token'])
        response = self.client.get(url)
        self.assertRedirects(response, reverse('accounts:login'))
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_account_delete_done_get_returns_200(self):
        response = self.client.get(self.done_url)
        self.assertEqual(response.status_code, 200)


class DeleteExpiredAccountsCommandTests(TestCase):
    """Tests for delete_expired_accounts management command."""

    def test_deletes_user_requested_91_days_ago(self):
        from datetime import timedelta
        from django.core.management import call_command
        from io import StringIO
        user = CustomUser.objects.create_user(
            email='old@example.com',
            password='Pass123',
        )
        user.deletion_requested_at = timezone.now() - timedelta(days=91)
        user.save()
        out = StringIO()
        call_command('delete_expired_accounts', stdout=out)
        self.assertFalse(CustomUser.objects.filter(email='old@example.com').exists())
        self.assertIn('Deleted', out.getvalue())

    def test_does_not_delete_user_requested_89_days_ago(self):
        from datetime import timedelta
        from django.core.management import call_command
        from io import StringIO
        user = CustomUser.objects.create_user(
            email='recent@example.com',
            password='Pass123',
        )
        user.deletion_requested_at = timezone.now() - timedelta(days=89)
        user.save()
        out = StringIO()
        call_command('delete_expired_accounts', stdout=out)
        self.assertTrue(CustomUser.objects.filter(email='recent@example.com').exists())

    def test_does_not_delete_user_with_no_deletion_requested(self):
        from django.core.management import call_command
        from io import StringIO
        user = CustomUser.objects.create_user(
            email='active@example.com',
            password='Pass123',
        )
        self.assertIsNone(user.deletion_requested_at)
        out = StringIO()
        call_command('delete_expired_accounts', stdout=out)
        self.assertTrue(CustomUser.objects.filter(email='active@example.com').exists())

    def test_dry_run_does_not_delete(self):
        from datetime import timedelta
        from django.core.management import call_command
        from io import StringIO
        user = CustomUser.objects.create_user(
            email='dryrun@example.com',
            password='Pass123',
        )
        user.deletion_requested_at = timezone.now() - timedelta(days=91)
        user.save()
        out = StringIO()
        call_command('delete_expired_accounts', '--dry-run', stdout=out)
        self.assertTrue(CustomUser.objects.filter(email='dryrun@example.com').exists())
        self.assertIn('Dry run', out.getvalue())
        self.assertIn('would delete', out.getvalue())


class LogoutTests(TestCase):
    """Tests for logout view."""

    def setUp(self):
        self.client = Client()
        self.logout_url = reverse('accounts:logout')

    def test_post_logs_out_and_redirects(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self.assertTrue(auth.get_user(self.client).is_authenticated)
        response = self.client.post(self.logout_url)
        self.assertRedirects(response, reverse('accounts:login'))
        self.assertFalse(auth.get_user(self.client).is_authenticated)


class ProfileViewTests(TestCase):
    """Tests for ProfileView."""

    def setUp(self):
        self.client = Client()
        self.profile_url = reverse('accounts:profile')

    def test_get_anonymous_redirects_to_login(self):
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.get('Location', ''))

    def _mark_onboarding_complete(self, user):
        prefs, _ = UserPreferences.objects.get_or_create(user=user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_get_logged_in_returns_200_with_forms(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('user_info_form', response.context)
        self.assertIn('user_profile_form', response.context)

    def test_profile_includes_transcription_text_size_selector(self):
        """Profile page includes transcription size buttons (Small, Medium, Large) with instant-apply."""
        user = CustomUser.objects.create_user(
            email='sizeprofile@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('data-transcription-size="small"', content)
        self.assertIn('data-transcription-size="medium"', content)
        self.assertIn('data-transcription-size="large"', content)
        self.assertIn('VDTheme.selectTranscriptionSize', content)

    def test_post_valid_updates_and_redirects(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.profile_url, {
            'first_name': 'John',
            'last_name': 'Doe',
            'bio': 'Hello',
            'phone_number': '',
            'location': '',
            'website': '',
        })
        self.assertRedirects(response, self.profile_url)
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'John')
        self.assertEqual(user.last_name, 'Doe')
        self.assertEqual(user.profile.bio, 'Hello')

    def test_post_invalid_returns_200_with_errors(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.profile_url, {
            'first_name': 'John',
            'last_name': 'Doe',
            'bio': 'Hello',
            'phone_number': '',
            'location': '',
            'website': 'not-a-valid-url',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['user_profile_form'].errors)

    def test_post_save_preferences_updates_show_recording_timer(self):
        """POST with save_preferences should save show_recording_timer to UserPreferences."""
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)

        prefs = UserPreferences.objects.get(user=user)
        prefs.show_recording_timer = True
        prefs.save()

        response = self.client.post(self.profile_url, {
            'save_preferences': '1',
            'preferred_language': 'en',
            'drive_attachment_folder_name': 'VoiceDiaryFiles/attachments',
            'show_recording_timer': 'on',
            'timezone': 'Europe/Lisbon',
        })
        self.assertRedirects(response, self.profile_url + '#voice-diary')
        prefs.refresh_from_db()
        self.assertTrue(prefs.show_recording_timer)

        response2 = self.client.post(self.profile_url, {
            'save_preferences': '1',
            'preferred_language': 'en',
            'drive_attachment_folder_name': 'VoiceDiaryFiles/attachments',
            'timezone': 'Europe/Lisbon',
        })
        self.assertRedirects(response2, self.profile_url + '#voice-diary')
        prefs.refresh_from_db()
        self.assertFalse(prefs.show_recording_timer)

    def test_preferences_form_saves_timezone(self):
        """Saving the preferences form with a new timezone persists the value."""
        user = CustomUser.objects.create_user(
            email='tzformuser@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.profile_url, {
            'save_preferences': '1',
            'preferred_language': 'en',
            'drive_attachment_folder_name': 'VoiceDiaryFiles/attachments',
            'timezone': 'Europe/Paris',
        })
        self.assertRedirects(response, self.profile_url + '#voice-diary')
        prefs = UserPreferences.objects.get(user=user)
        self.assertEqual(prefs.timezone, 'Europe/Paris')

    def test_preferences_form_rejects_invalid_timezone(self):
        """Submitting an invalid timezone choice is rejected by form validation."""
        user = CustomUser.objects.create_user(
            email='tzbaduser@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.profile_url, {
            'save_preferences': '1',
            'preferred_language': 'en',
            'timezone': 'Not/ATimezone',
        })
        self.assertEqual(response.status_code, 200)  # no redirect = form error
        prefs = UserPreferences.objects.get(user=user)
        self.assertEqual(prefs.timezone, 'Europe/Lisbon')  # unchanged


class UpdateThemePreferencesTests(TestCase):
    """Tests for update_theme_preferences API (instant-save prefs like accent, transcription size)."""

    def setUp(self):
        self.client = Client()
        self.theme_url = reverse('accounts:update_theme')
        self.user = CustomUser.objects.create_user(
            email='themetest@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs, _ = UserPreferences.objects.get_or_create(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_post_transcription_text_size_saves_to_preferences(self):
        """POST with transcription_text_size saves to UserPreferences without form Save."""
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        self.assertEqual(prefs.transcription_text_size, 'small')

        response = self.client.post(
            self.theme_url,
            data='{"transcription_text_size": "medium"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        prefs.refresh_from_db()
        self.assertEqual(prefs.transcription_text_size, 'medium')

        response2 = self.client.post(
            self.theme_url,
            data='{"transcription_text_size": "large"}',
            content_type='application/json',
        )
        self.assertEqual(response2.status_code, 200)
        prefs.refresh_from_db()
        self.assertEqual(prefs.transcription_text_size, 'large')

    def test_post_transcription_text_size_invalid_ignored(self):
        """Invalid transcription_text_size values are ignored."""
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.transcription_text_size = 'medium'
        prefs.save()

        response = self.client.post(
            self.theme_url,
            data='{"transcription_text_size": "invalid"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        prefs.refresh_from_db()
        self.assertEqual(prefs.transcription_text_size, 'medium')

    def test_post_requires_login(self):
        """update_theme_preferences requires authenticated user."""
        response = self.client.post(
            self.theme_url,
            data='{"transcription_text_size": "medium"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 302)


class SetInterfaceLanguageTests(TestCase):
    """Tests for set_interface_language view."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('accounts:set_interface_language')
        self.user = CustomUser.objects.create_user(
            email='langtest@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs, _ = UserPreferences.objects.get_or_create(user=self.user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_post_authenticated_saves_to_preferences(self):
        """POST with valid language saves to UserPreferences.interface_language."""
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        self.assertEqual(prefs.interface_language, 'pt-pt')

        next_url = reverse('accounts:profile')
        response = self.client.post(
            self.url,
            data={'language': 'en', 'next': next_url},
        )
        self.assertRedirects(response, next_url)
        prefs.refresh_from_db()
        self.assertEqual(prefs.interface_language, 'en')

    def test_post_authenticated_sets_cookie(self):
        """POST sets django_language cookie."""
        self.client.force_login(self.user)
        cookie_name = getattr(settings, 'LANGUAGE_COOKIE_NAME', 'django_language')
        response = self.client.post(
            self.url,
            data={'language': 'en', 'next': '/'},
        )
        self.assertIn(cookie_name, response.cookies)
        self.assertEqual(response.cookies[cookie_name].value, 'en')

    def test_post_invalid_language_redirects_without_saving(self):
        """Invalid language redirects without saving to preferences."""
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.interface_language = 'pt-pt'
        prefs.save()

        response = self.client.post(
            self.url,
            data={'language': 'invalid', 'next': '/'},
        )
        self.assertRedirects(response, '/')
        prefs.refresh_from_db()
        self.assertEqual(prefs.interface_language, 'pt-pt')

    def test_post_anonymous_sets_cookie_only(self):
        """Anonymous user can set language; cookie is set, no DB save."""
        response = self.client.post(
            self.url,
            data={'language': 'en', 'next': '/'},
        )
        self.assertRedirects(response, '/')
        cookie_name = getattr(settings, 'LANGUAGE_COOKIE_NAME', 'django_language')
        self.assertIn(cookie_name, response.cookies)


class UserInterfaceLanguageMiddlewareTests(TestCase):
    """Tests for UserInterfaceLanguageMiddleware."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email='mwlang@example.com',
            password='Pass123',
        )
        self.user.is_email_verified = True
        self.user.save()
        prefs, _ = UserPreferences.objects.get_or_create(user=self.user)
        prefs.onboarding_completed = True
        prefs.interface_language = 'en'
        prefs.save()

    def test_authenticated_user_gets_language_from_prefs(self):
        """Middleware activates interface_language from UserPreferences and page renders in that language."""
        self.client.force_login(self.user)
        prefs = UserPreferences.objects.get(user=self.user)
        prefs.interface_language = 'en'
        prefs.save()

        response = self.client.get(reverse('accounts:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Profile', response.content)


class ThemePreferencesContextProcessorTests(TestCase):
    """Tests for theme_preferences context processor."""

    def test_authenticated_user_gets_transcription_text_size_from_prefs(self):
        """Context processor includes transcription_text_size from UserPreferences."""
        from src.accounts.context_processors import theme_preferences
        from django.test import RequestFactory

        user = CustomUser.objects.create_user(
            email='ctx@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()

        prefs = UserPreferences.objects.get(user=user)
        prefs.transcription_text_size = 'large'
        prefs.save()

        request = RequestFactory().get('/')
        request.user = user
        ctx = theme_preferences(request)
        self.assertEqual(ctx['transcription_text_size'], 'large')

    def test_anonymous_user_gets_default_transcription_text_size(self):
        """Context processor returns 'small' when not authenticated."""
        from src.accounts.context_processors import theme_preferences
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        ctx = theme_preferences(request)
        self.assertEqual(ctx['transcription_text_size'], 'small')


class PasswordResetTests(TestCase):
    """Tests for password reset flow."""

    def setUp(self):
        self.client = Client()
        self.reset_url = reverse('accounts:password_reset')
        self.reset_done_url = reverse('accounts:password_reset_done')
        self.reset_complete_url = reverse('accounts:password_reset_complete')

    def test_get_returns_200_with_form(self):
        response = self.client.get(self.reset_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)

    @patch('src.accounts.forms.CustomPasswordResetForm.send_mail')
    def test_post_valid_email_sends_mail_and_redirects_to_done(self, mock_send_mail):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='Pass123',
        )
        user.is_email_verified = True
        user.save()
        response = self.client.post(self.reset_url, {'email': 'user@example.com'})
        self.assertRedirects(response, self.reset_done_url)
        mock_send_mail.assert_called_once()


class PasswordResetConfirmTests(TestCase):
    """Tests for password reset confirm (valid token sets password; invalid shows error)."""

    def setUp(self):
        self.client = Client()
        self.reset_url = reverse('accounts:password_reset')
        self.complete_url = reverse('accounts:password_reset_complete')

    @patch('src.accounts.forms.CustomPasswordResetForm.send_mail')
    def test_valid_token_sets_password_and_can_login(self, mock_send_mail):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='OldPass123',
        )
        user.is_email_verified = True
        user.save()
        response = self.client.post(self.reset_url, {'email': 'user@example.com'})
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        mock_send_mail.assert_called_once()
        call_args = mock_send_mail.call_args[0]
        context = call_args[2]
        uidb64 = context['uid']
        token = context['token']
        confirm_url = reverse('accounts:password_reset_confirm', args=[uidb64, token])
        get_response = self.client.get(confirm_url)
        self.assertEqual(get_response.status_code, 302)
        set_password_url = get_response['Location']
        response = self.client.post(set_password_url, {
            'new_password1': 'NewPass456',
            'new_password2': 'NewPass456',
        })
        self.assertRedirects(response, self.complete_url)
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewPass456'))
        self.client.force_login(user)
        self.assertTrue(auth.get_user(self.client).is_authenticated)

    def test_invalid_token_does_not_change_password(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='OldPass123',
        )
        user.is_email_verified = True
        user.save()
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        url = reverse('accounts:password_reset_confirm', args=[uidb64, 'invalid-token'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('OldPass123'))


class PasswordChangeTests(TestCase):
    """Tests for password change (logged-in)."""

    def setUp(self):
        self.client = Client()
        self.change_url = reverse('accounts:password_change')
        self.change_done_url = reverse('accounts:password_change_done')

    def _mark_onboarding_complete(self, user):
        prefs, _ = UserPreferences.objects.get_or_create(user=user)
        prefs.onboarding_completed = True
        prefs.save()

    def test_correct_old_and_new_password_succeeds(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='OldPass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.change_url, {
            'old_password': 'OldPass123',
            'new_password1': 'NewPass456',
            'new_password2': 'NewPass456',
        })
        self.assertRedirects(response, self.change_done_url)
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewPass456'))

    def test_wrong_old_password_fails(self):
        user = CustomUser.objects.create_user(
            email='user@example.com',
            password='OldPass123',
        )
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.post(self.change_url, {
            'old_password': 'WrongOld',
            'new_password1': 'NewPass456',
            'new_password2': 'NewPass456',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        user.refresh_from_db()
        self.assertTrue(user.check_password('OldPass123'))

    def test_google_only_user_redirected_from_password_change(self):
        user = CustomUser.objects.create_user(
            email='google@example.com',
            password='dummy',
        )
        user.set_unusable_password()
        user.is_google_account = True
        user.is_email_verified = True
        user.save()
        self.client.force_login(user)
        self._mark_onboarding_complete(user)
        response = self.client.get(self.change_url)
        self.assertRedirects(response, reverse('accounts:profile'))


class CheckEmailAvailabilityTests(TestCase):
    """Tests for check_email_availability API."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('accounts:check_email')

    def test_existing_email_returns_available_false(self):
        CustomUser.objects.create_user(
            email='existing@example.com',
            password='Pass123',
        )
        response = self.client.get(self.url, {'email': 'existing@example.com'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['available'])

    def test_new_email_returns_available_true(self):
        response = self.client.get(self.url, {'email': 'newuser@example.com'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['available'])

    def test_no_email_returns_400_with_error(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('error', data)
