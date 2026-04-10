"""
Tests for src/common/google_account/auth.py

Tests critical OAuth and token management functions:
- exchange_code_for_tokens()
- store_user_tokens()
- get_user_tokens()
- verify_drive_permissions()
"""

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch, MagicMock, Mock
from urllib.error import HTTPError, URLError

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

from src.accounts.models import UserSecret
from src.common.google_account.auth import (
    exchange_code_for_tokens,
    store_user_tokens,
    verify_drive_permissions,
    verify_gmail_permissions,
    GoogleAuthError,
    get_authenticated_service,
)

User = get_user_model()


class ExchangeCodeForTokensTestCase(TestCase):
    """Test exchange_code_for_tokens() function."""
    
    def setUp(self):
        self.factory = RequestFactory()
        self.valid_code = "test_auth_code_12345"
        
    def _mock_token_response(self):
        """Create a mock successful token response."""
        return {
            'access_token': 'access_token_value',
            'refresh_token': 'refresh_token_value',
            'expires_in': 3600,
            'token_type': 'Bearer',
            'scope': 'https://www.googleapis.com/auth/gmail.readonly'
        }
    
    @patch('src.common.google_account.auth.urlopen')
    @patch('src.common.google_account.auth.get_google_client_config')
    @patch('src.common.google_account.auth.get_redirect_uri')
    def test_exchange_code_success(self, mock_redirect, mock_config, mock_urlopen):
        """Test successful authorization code exchange."""
        mock_config.return_value = {
            'client_id': 'test_client_id',
            'client_secret': 'test_client_secret'
        }
        mock_redirect.return_value = 'http://localhost:8000/callback/'
        
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(self._mock_token_response()).encode('utf-8')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response
        
        request = self.factory.get('/callback/', {'code': self.valid_code})
        
        result = exchange_code_for_tokens(self.valid_code, request)
        
        self.assertEqual(result['access_token'], 'access_token_value')
        self.assertEqual(result['refresh_token'], 'refresh_token_value')
        self.assertEqual(result['expires_in'], 3600)
        self.assertIn('access_token', result)
    
    @patch('src.common.google_account.auth.urlopen')
    @patch('src.common.google_account.auth.get_google_client_config')
    @patch('src.common.google_account.auth.get_redirect_uri')
    def test_exchange_code_invalid_code(self, mock_redirect, mock_config, mock_urlopen):
        """Test exchange with invalid authorization code."""
        mock_config.return_value = {
            'client_id': 'test_client_id',
            'client_secret': 'test_client_secret'
        }
        mock_redirect.return_value = 'http://localhost:8000/callback/'
        
        error_response = b'{"error": "invalid_code"}'
        http_error = HTTPError(
            url='https://oauth2.googleapis.com/token',
            code=400,
            msg='Bad Request',
            hdrs={},
            fp=None
        )
        http_error.fp = Mock()
        http_error.fp.read.return_value = error_response
        mock_urlopen.side_effect = http_error
        
        request = self.factory.get('/callback/', {'code': 'invalid_code'})
        
        with self.assertRaises(GoogleAuthError) as context:
            exchange_code_for_tokens('invalid_code', request)
        
        self.assertIn('Failed to exchange code', str(context.exception))
    
    @patch('src.common.google_account.auth.urlopen')
    @patch('src.common.google_account.auth.get_google_client_config')
    @patch('src.common.google_account.auth.get_redirect_uri')
    def test_exchange_code_network_error(self, mock_redirect, mock_config, mock_urlopen):
        """Test exchange with network timeout."""
        mock_config.return_value = {
            'client_id': 'test_client_id',
            'client_secret': 'test_client_secret'
        }
        mock_redirect.return_value = 'http://localhost:8000/callback/'
        mock_urlopen.side_effect = TimeoutError("Connection timeout")
        
        request = self.factory.get('/callback/', {'code': self.valid_code})
        
        with self.assertRaises(GoogleAuthError) as context:
            exchange_code_for_tokens(self.valid_code, request)
        
        self.assertIn('Network error', str(context.exception))
    
    @patch('src.common.google_account.auth.urlopen')
    @patch('src.common.google_account.auth.get_google_client_config')
    @patch('src.common.google_account.auth.get_redirect_uri')
    def test_exchange_code_invalid_response(self, mock_redirect, mock_config, mock_urlopen):
        """Test exchange with malformed token response."""
        mock_config.return_value = {
            'client_id': 'test_client_id',
            'client_secret': 'test_client_secret'
        }
        mock_redirect.return_value = 'http://localhost:8000/callback/'
        
        mock_response = MagicMock()
        mock_response.read.return_value = b'not valid json {'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response
        
        request = self.factory.get('/callback/', {'code': self.valid_code})
        
        with self.assertRaises(GoogleAuthError) as context:
            exchange_code_for_tokens(self.valid_code, request)
        
        self.assertIn('Invalid response', str(context.exception))


class StoreUserTokensTestCase(TestCase):
    """Test store_user_tokens() function."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.user.save()
    
    @patch('src.common.google_account.auth.encrypt_value')
    def test_store_tokens_success(self, mock_encrypt):
        """Test successful token storage with encryption."""
        mock_encrypt.side_effect = lambda val: f"encrypted_{val}"
        
        access_token = "access_token_12345"
        refresh_token = "refresh_token_abcde"
        expires_in = 3600
        scopes = ['https://www.googleapis.com/auth/gmail.readonly', 
                  'https://www.googleapis.com/auth/drive']
        
        store_user_tokens(
            user=self.user,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            scopes=scopes
        )
        
        user_secret = UserSecret.objects.get(user=self.user)
        self.assertEqual(user_secret.encrypted_google_access_token, f"encrypted_{access_token}")
        self.assertEqual(user_secret.encrypted_google_refresh_token, f"encrypted_{refresh_token}")
        self.assertIsNotNone(user_secret.encrypted_google_token_expiry)

    
    @patch('src.common.google_account.auth.encrypt_value')
    def test_store_tokens_without_refresh_token(self, mock_encrypt):
        """Test storing tokens when refresh_token is None."""
        mock_encrypt.side_effect = lambda val: f"encrypted_{val}"
        
        access_token = "access_token_12345"
        scopes = ['https://www.googleapis.com/auth/gmail.readonly']
        
        store_user_tokens(
            user=self.user,
            access_token=access_token,
            refresh_token=None,
            expires_in=3600,
            scopes=scopes
        )
        
        user_secret = UserSecret.objects.get(user=self.user)
        self.assertEqual(user_secret.encrypted_google_access_token, f"encrypted_{access_token}")
        self.assertIsNone(user_secret.encrypted_google_refresh_token)

    
    @patch('src.common.google_account.auth.encrypt_value')
    def test_store_tokens_scopes_saved(self, mock_encrypt):
        """Test that scopes are properly saved to UserSecret."""
        mock_encrypt.side_effect = lambda val: f"encrypted_{val}"
        
        scopes = ['https://www.googleapis.com/auth/gmail.readonly',
                  'https://www.googleapis.com/auth/drive',
                  'https://www.googleapis.com/auth/calendar']
        
        store_user_tokens(
            user=self.user,
            access_token="access_token",
            refresh_token="refresh_token",
            expires_in=3600,
            scopes=scopes
        )
        
        user_secret = UserSecret.objects.get(user=self.user)
        saved_scopes = user_secret.get_scopes_list()
        self.assertEqual(set(saved_scopes), set(scopes))


class GetAuthenticatedServiceTestCase(TestCase):
    """Test get_authenticated_service() function."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.user.save()
    
    @patch('src.common.google_account.auth._get_user_credentials')
    @patch('src.common.google_account.auth.build')
    def test_get_authenticated_service_success(self, mock_build, mock_get_creds):
        """Test successful authenticated service creation."""
        mock_creds = MagicMock()
        mock_creds.expiry = None
        mock_creds.refresh_token = None
        mock_get_creds.return_value = mock_creds
        
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        result = get_authenticated_service(self.user, 'drive')
        
        self.assertIsNotNone(result)
        mock_get_creds.assert_called_once()
    
    @patch('src.common.google_account.auth._get_user_credentials')
    def test_get_service_no_credentials(self, mock_get_creds):
        """Test error when user has no credentials."""
        mock_get_creds.return_value = None
        
        with self.assertRaises(GoogleAuthError):
            get_authenticated_service(self.user, 'drive')
    
    def test_invalid_service_type(self):
        """Test that invalid service type raises ValueError."""
        with self.assertRaises(ValueError):
            get_authenticated_service(self.user, 'invalid_service')


class VerifyDrivePermissionsTestCase(TestCase):
    """Test verify_drive_permissions() function."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.user.save()
    
    def test_verify_drive_permissions_no_secret(self):
        """Test that user without UserSecret has no Drive permissions."""
        result = verify_drive_permissions(self.user)
        self.assertFalse(result)
    
    def test_verify_drive_permissions_no_tokens(self):
        """Test that user with UserSecret but no tokens has no permissions."""
        UserSecret.objects.create(user=self.user)
        result = verify_drive_permissions(self.user)
        self.assertFalse(result)
    
    def test_verify_drive_permissions_has_drive_scope(self):
        """Test that user with Drive scope in tokens has permissions."""
        user_secret = UserSecret.objects.create(user=self.user)
        user_secret.set_scopes_list([
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/gmail.readonly'
        ])
        user_secret.encrypted_google_access_token = 'encrypted_access'
        user_secret.save()
        
        result = verify_drive_permissions(self.user)
        self.assertTrue(result)
    
    def test_verify_drive_permissions_no_drive_scope(self):
        """Test that user without Drive scope has no permissions."""
        user_secret = UserSecret.objects.create(user=self.user)
        user_secret.set_scopes_list([
            'https://www.googleapis.com/auth/gmail.readonly'
        ])
        user_secret.encrypted_google_access_token = 'encrypted_access'
        user_secret.save()
        
        result = verify_drive_permissions(self.user)
        self.assertFalse(result)


class VerifyGmailPermissionsTestCase(TestCase):
    """Test verify_gmail_permissions() function."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='gmail_test@example.com',
            password='testpass123'
        )
        self.user.save()

    def test_verify_gmail_permissions_no_secret(self):
        """Test that user without UserSecret has no Gmail permissions."""
        result = verify_gmail_permissions(self.user)
        self.assertFalse(result)

    def test_verify_gmail_permissions_has_gmail_scope(self):
        """Test that user with Gmail scope has permissions."""
        user_secret = UserSecret.objects.create(user=self.user)
        user_secret.set_scopes_list([
            'https://www.googleapis.com/auth/gmail.modify',
        ])
        user_secret.encrypted_google_access_token = 'encrypted_access'
        user_secret.save()
        result = verify_gmail_permissions(self.user)
        self.assertTrue(result)

    def test_verify_gmail_permissions_no_gmail_scope(self):
        """Test that user without Gmail scope has no permissions."""
        user_secret = UserSecret.objects.create(user=self.user)
        user_secret.set_scopes_list([
            'https://www.googleapis.com/auth/drive',
        ])
        user_secret.encrypted_google_access_token = 'encrypted_access'
        user_secret.save()
        result = verify_gmail_permissions(self.user)
        self.assertFalse(result)
