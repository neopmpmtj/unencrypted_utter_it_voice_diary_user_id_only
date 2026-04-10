"""
User-Aware Google API Authentication Module

This module provides Google API authentication using per-user OAuth tokens
stored in the Django database. It handles automatic token refresh and
credential management for individual users.

Key Features:
- OAuth authorization flow (create URL, exchange code)
- Per-user authentication using stored OAuth tokens
- Automatic token refresh when expired
- Encrypted token storage and retrieval
- Support for Gmail, Drive, and Calendar APIs
- User info retrieval from Google

Version: 2.0.0
"""

import json
import secrets
from datetime import datetime, timezone as dt_timezone
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen, Request as URLRequest
from urllib.error import URLError, HTTPError

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from django.conf import settings

from src.common.utils.encryption import encrypt_value, decrypt_value
from src.common.logging_utils.logging_config import get_logger
from .config import (
    config, 
    LOGIN_SCOPES, 
    FULL_SCOPES, 
    SERVICE_SCOPES,
    GOOGLE_AUTH_URI,
    GOOGLE_TOKEN_URI,
    GOOGLE_USERINFO_URI,
)

logger = get_logger('google_auth')

GOOGLE_API_TIMEOUT = 30


class _TimeoutRequest(Request):
    """Wraps google-auth Request to enforce a default timeout on every HTTP call,
    including internal calls made by Credentials.refresh() which pass no timeout."""

    def __init__(self, session=None, timeout=GOOGLE_API_TIMEOUT):
        super().__init__(session=session)
        self._default_timeout = timeout

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        if timeout is None:
            timeout = self._default_timeout
        return super().__call__(url, method, body, headers, timeout=timeout, **kwargs)


class GoogleAuthError(Exception):
    """Custom exception for Google authentication errors."""
    pass


DEFAULT_REQUIRED_SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',
]


# ============================================================================
# OAUTH CONFIGURATION HELPERS
# ============================================================================

def get_google_client_config() -> Dict[str, Any]:
    """
    Get Google OAuth client configuration from Django settings.
    
    Returns:
        Dict containing client_id and client_secret
        
    Raises:
        GoogleAuthError: If credentials are not configured
    """
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', None)
    client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', None)
    
    if not client_id or not client_secret:
        raise GoogleAuthError(
            "Google OAuth credentials not configured. "
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in settings."
        )
    
    return {
        'client_id': client_id,
        'client_secret': client_secret,
    }


def get_redirect_uri(request=None) -> str:
    """
    Get the OAuth callback redirect URI.
    
    Args:
        request: Optional Django request to build absolute URI
        
    Returns:
        str: The redirect URI for OAuth callback
    """
    redirect_uri = getattr(settings, 'GOOGLE_OAUTH_REDIRECT_URI', None)
    
    if redirect_uri:
        return redirect_uri
    
    # Build from request if available
    if request:
        from django.urls import reverse
        callback_path = reverse('accounts:google_callback')
        return request.build_absolute_uri(callback_path)
    
    # Fallback for development when no request/settings; path must match project URL prefix
    return 'http://localhost:8000/src.accounts/google/callback/'


# ============================================================================
# OAUTH FLOW METHODS
# ============================================================================

def create_authorization_url(
    request=None,
    scopes: Optional[list] = None,
    state: Optional[str] = None,
    login_hint: Optional[str] = None,
    include_granted_scopes: bool = True,
) -> Tuple[str, str]:
    """
    Create a Google OAuth authorization URL.
    
    This initiates the OAuth flow by generating a URL that redirects
    the user to Google's consent screen.
    
    Args:
        request: Django request object (for building redirect URI)
        scopes: List of OAuth scopes to request. Defaults to FULL_SCOPES.
        state: Optional state parameter for CSRF protection. 
               If not provided, a secure random state is generated.
        login_hint: Optional email to pre-fill in Google's login form
        include_granted_scopes: If True, include previously granted scopes
        
    Returns:
        Tuple of (authorization_url, state)
        
    Raises:
        GoogleAuthError: If client configuration is invalid
        
    Example:
        >>> auth_url, state = create_authorization_url(request)
        >>> request.session['oauth_state'] = state
        >>> return redirect(auth_url)
    """
    client_config = get_google_client_config()
    redirect_uri = get_redirect_uri(request)
    
    # Use provided scopes or default to full scopes
    request_scopes = scopes or FULL_SCOPES
    
    # Generate state if not provided
    if state is None:
        state = secrets.token_urlsafe(32)
    
    # Build authorization URL parameters
    params = {
        'client_id': client_config['client_id'],
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(request_scopes),
        'state': state,
        'access_type': 'offline',  # Request refresh token
        'prompt': 'consent',  # Always show consent to get refresh token
    }
    
    if login_hint:
        params['login_hint'] = login_hint
    
    if include_granted_scopes:
        params['include_granted_scopes'] = 'true'
    
    authorization_url = f"{GOOGLE_AUTH_URI}?{urlencode(params)}"
    
    logger.debug(f"Created authorization URL with scopes: {request_scopes}")
    
    return authorization_url, state


def exchange_code_for_tokens(
    code: str,
    request=None,
) -> Dict[str, Any]:
    """
    Exchange an authorization code for OAuth tokens.
    
    After the user authorizes on Google's consent screen, Google redirects
    back with an authorization code. This function exchanges that code
    for access and refresh tokens.
    
    Args:
        code: The authorization code from Google's callback
        request: Django request object (for building redirect URI)
        
    Returns:
        Dict containing:
            - access_token: Token for API calls
            - refresh_token: Token for refreshing access (may be None)
            - expires_in: Seconds until access token expires
            - token_type: Usually "Bearer"
            - scope: Space-separated granted scopes
            
    Raises:
        GoogleAuthError: If token exchange fails
        
    Example:
        >>> tokens = exchange_code_for_tokens(request.GET['code'], request)
        >>> access_token = tokens['access_token']
    """
    client_config = get_google_client_config()
    redirect_uri = get_redirect_uri(request)
    
    # Prepare token exchange request
    token_data = {
        'code': code,
        'client_id': client_config['client_id'],
        'client_secret': client_config['client_secret'],
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }
    
    try:
        # Make token exchange request
        data = urlencode(token_data).encode('utf-8')
        req = URLRequest(GOOGLE_TOKEN_URI, data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        
        with urlopen(req, timeout=30) as response:
            token_response = json.loads(response.read().decode('utf-8'))
        
        logger.info("Successfully exchanged authorization code for tokens")
        return token_response
        
    except HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else str(e)
        logger.error(f"Token exchange failed (HTTP {e.code}): {error_body}")
        raise GoogleAuthError(f"Failed to exchange code for tokens: {error_body}")
        
    except (URLError, TimeoutError) as e:
        logger.error(f"Token exchange network error: {e}")
        raise GoogleAuthError(f"Network error during token exchange: {e}")
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid token response: {e}")
        raise GoogleAuthError("Invalid response from Google token endpoint")


def get_google_user_info(access_token: str) -> Dict[str, Any]:
    """
    Fetch user information from Google using an access token.
    
    Args:
        access_token: A valid Google OAuth access token
        
    Returns:
        Dict containing user info:
            - sub: Google user ID (unique identifier)
            - email: User's email address
            - email_verified: Whether email is verified by Google
            - name: Full name (may be empty)
            - given_name: First name (may be empty)
            - family_name: Last name (may be empty)
            - picture: Profile picture URL (may be empty)
            
    Raises:
        GoogleAuthError: If user info fetch fails
        
    Example:
        >>> user_info = get_google_user_info(tokens['access_token'])
        >>> email = user_info['email']
        >>> name = user_info.get('name', '')
    """
    try:
        req = URLRequest(GOOGLE_USERINFO_URI)
        req.add_header('Authorization', f'Bearer {access_token}')
        
        with urlopen(req, timeout=30) as response:
            user_info = json.loads(response.read().decode('utf-8'))
        
        logger.debug(f"Fetched user info for: {user_info.get('email', 'unknown')}")
        return user_info
        
    except HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else str(e)
        logger.error(f"User info fetch failed (HTTP {e.code}): {error_body}")
        raise GoogleAuthError(f"Failed to fetch user info: {error_body}")
        
    except (URLError, TimeoutError) as e:
        logger.error(f"User info fetch network error: {e}")
        raise GoogleAuthError(f"Network error fetching user info: {e}")
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid user info response: {e}")
        raise GoogleAuthError("Invalid response from Google userinfo endpoint")


def store_user_tokens(
    user,
    access_token: str,
    refresh_token: Optional[str],
    expires_in: Optional[int],
    scopes: list,
) -> None:
    """
    Store OAuth tokens for a user in UserSecret.
    
    Tokens are encrypted before storage using MASTER_ENCRYPTION_KEY (Fernet).
    
    Args:
        user: Django User instance
        access_token: The OAuth access token
        refresh_token: The OAuth refresh token (may be None)
        expires_in: Seconds until access token expires
        scopes: List of granted scopes
        
    Example:
        >>> store_user_tokens(
        ...     user=user,
        ...     access_token=tokens['access_token'],
        ...     refresh_token=tokens.get('refresh_token'),
        ...     expires_in=tokens.get('expires_in'),
        ...     scopes=tokens['scope'].split(' ')
        ... )
    """
    from src.accounts.models import UserSecret
    
    # Get or create UserSecret
    user_secret, created = UserSecret.objects.get_or_create(user=user)
    
    # Encrypt and store access token
    user_secret.encrypted_google_access_token = encrypt_value(access_token)
    
    # Encrypt and store refresh token if provided
    if refresh_token:
        user_secret.encrypted_google_refresh_token = encrypt_value(refresh_token)
    
    # Calculate and store expiry
    if expires_in:
        expiry = datetime.now(dt_timezone.utc) + __import__('datetime').timedelta(seconds=expires_in)
        # Store as naive UTC datetime (Google auth library expects this)
        expiry_naive = expiry.replace(tzinfo=None)
        user_secret.encrypted_google_token_expiry = encrypt_value(expiry_naive.isoformat())
    
    # Store granted scopes
    user_secret.set_scopes_list(scopes)
    
    user_secret.save()
    
    logger.info(f"Stored OAuth tokens for user {user.id}")


def revoke_user_tokens(user) -> bool:
    """
    Revoke a user's Google OAuth tokens.
    
    This invalidates the tokens with Google and clears them from storage.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if revocation succeeded, False otherwise
    """
    from src.accounts.models import UserSecret
    from .config import GOOGLE_REVOKE_URI
    
    try:
        user_secret = UserSecret.objects.filter(user=user).first()
        if not user_secret or not user_secret.encrypted_google_access_token:
            logger.warning(f"No tokens to revoke for user {user.id}")
            return True
        
        # Get access token
        access_token = decrypt_value(user_secret.encrypted_google_access_token)
        
        if access_token:
            # Revoke with Google
            try:
                data = urlencode({'token': access_token}).encode('utf-8')
                req = URLRequest(GOOGLE_REVOKE_URI, data=data, method='POST')
                req.add_header('Content-Type', 'application/x-www-form-urlencoded')
                
                with urlopen(req, timeout=30) as response:
                    pass  # Success
                    
                logger.info(f"Revoked Google tokens for user {user.id}")
                
            except HTTPError as e:
                # 400 means token already revoked/invalid, which is fine
                if e.code != 400:
                    logger.warning(f"Token revocation returned HTTP {e.code}")
        
        # Clear stored tokens
        user_secret.encrypted_google_access_token = None
        user_secret.encrypted_google_refresh_token = None
        user_secret.encrypted_google_token_expiry = None
        user_secret.google_token_scopes = None
        user_secret.save()
        
        return True
        
    except Exception as e:
        logger.error(f"Error revoking tokens for user {user.id}: {e}")
        return False


def fetch_granted_scopes_from_access_token(access_token: str, timeout_seconds: int = 10) -> list[str]:
    """
    Fetch the *granted* OAuth scopes for an access token using Google's tokeninfo endpoint.

    Why this exists:
    - django-allauth settings define the *requested* scopes.
    - For strict permission enforcement, we need the *granted* scopes (authoritative source),
      not a fallback to what we asked for.

    Args:
        access_token: OAuth access token string.
        timeout_seconds: Network timeout for the tokeninfo call.

    Returns:
        List of granted scopes (may be empty if tokeninfo is unavailable).

    Raises:
        GoogleAuthError: If the token is invalid or tokeninfo returns an error response.
    """
    if not access_token:
        return []

    # Prefer oauth2.googleapis.com; it returns JSON including a space-delimited `scope` string.
    base_url = 'https://oauth2.googleapis.com/tokeninfo'
    query = urlencode({'access_token': access_token})
    url = f"{base_url}?{query}"

    try:
        with urlopen(url, timeout=timeout_seconds) as resp:
            payload = resp.read().decode('utf-8')
        data = json.loads(payload)
    except HTTPError as e:
        # Common for invalid/expired token: 400
        raise GoogleAuthError(f"Token introspection failed (HTTP {e.code}).")
    except (URLError, TimeoutError) as e:
        logger.warning(f"Token introspection unavailable: {e}")
        return []
    except json.JSONDecodeError as e:
        logger.warning(f"Token introspection returned invalid JSON: {e}")
        return []

    scope_str = data.get('scope', '') or ''
    scopes = [s for s in scope_str.split(' ') if s]
    return scopes


def verify_required_scopes(user, required_scopes: Optional[list[str]] = None) -> bool:
    """
    Verify that a user has granted all required Google OAuth scopes.

    This checks `UserSecret.google_token_scopes` which should reflect *granted* scopes
    (populated via tokeninfo). If scopes are missing/unavailable, this returns False.

    Args:
        user: Django User instance.
        required_scopes: Scopes that must be present. Defaults to DEFAULT_REQUIRED_SCOPES.

    Returns:
        True if all required scopes are present, else False.
    """
    required = required_scopes or DEFAULT_REQUIRED_SCOPES
    try:
        from src.accounts.models import UserSecret

        user_secret = UserSecret.objects.filter(user=user).first()
        if not user_secret:
            logger.warning(f"No UserSecret found for user {user.id}")
            return False

        return user_secret.has_required_scopes(required)
    except Exception as e:
        logger.error(f"Error verifying required scopes for user {user.id}: {e}")
        return False


def get_missing_required_scopes(user, required_scopes: Optional[list[str]] = None) -> list[str]:
    """
    Get list of missing required scopes for the user.

    Args:
        user: Django User instance.
        required_scopes: Scopes that must be present. Defaults to DEFAULT_REQUIRED_SCOPES.

    Returns:
        List of missing scope URLs.
    """
    required = required_scopes or DEFAULT_REQUIRED_SCOPES
    try:
        from src.accounts.models import UserSecret

        user_secret = UserSecret.objects.filter(user=user).first()
        if not user_secret:
            return required

        return user_secret.get_missing_scopes(required)
    except Exception as e:
        logger.error(f"Error getting missing required scopes for user {user.id}: {e}")
        return required


def get_authenticated_service(user, service_type: str = 'drive', cache: Optional[dict] = None):
    """
    Get an authenticated Google service instance for a specific user.
    
    This function:
    1. Checks cache if provided (avoids redundant service creation)
    2. Loads the user's stored OAuth tokens from UserSecret
    3. Creates Google Credentials object
    4. Refreshes token if expired
    5. Saves refreshed tokens back to database
    6. Builds and returns the requested Google API service
    7. Stores service in cache if provided
    
    Args:
        user: Django User instance
        service_type (str): Type of service ('drive', 'gmail', or 'calendar')
        cache (Optional[dict]): Optional cache dict to store/retrieve service objects.
                                Key format: "{user_id}:{service_type}"
        
    Returns:
        googleapiclient.discovery.Resource: Authenticated Google service
        
    Raises:
        GoogleAuthError: If user has no tokens or authentication fails
        ValueError: If invalid service_type provided
        
    Example:
        >>> drive_service = get_authenticated_service(request.user, 'drive')
        >>> gmail_service = get_authenticated_service(request.user, 'gmail')
        >>> # With caching:
        >>> cache = {}
        >>> service1 = get_authenticated_service(request.user, 'drive', cache)
        >>> service2 = get_authenticated_service(request.user, 'drive', cache)  # Returns cached
    """
    # Check cache first
    if cache is not None:
        cache_key = f"{user.id}:{service_type}"
        if cache_key in cache:
            logger.debug(f"Using cached Google {service_type} service for user {user.id}")
            return cache[cache_key]
    
    logger.debug(f"Building authenticated Google {service_type} service for user {user.id}")
    
    # Validate service type
    service_configs = {
        'drive': ('drive', 'v3'),
        'gmail': ('gmail', 'v1'),
        'calendar': ('calendar', 'v3'),
    }
    
    if service_type not in service_configs:
        raise ValueError(
            f"Invalid service_type: {service_type}. "
            f"Must be one of {list(service_configs.keys())}"
        )
    
    # Get user's credentials
    creds = _get_user_credentials(user)
    
    if not creds:
        raise GoogleAuthError(
            f"User {user.email} has not authorized Google API access. "
            "Please log in again to grant permissions."
        )
    
    # Validate token if refresh token available
    if creds.refresh_token:
        try:
            # Check if refresh is needed first
            needs_refresh = False
            if creds.expiry:
                # Use naive UTC datetime for comparison (Google auth library uses naive datetimes)
                now_utc_naive = datetime.now(dt_timezone.utc).replace(tzinfo=None)
                expiry_naive = creds.expiry
                if expiry_naive.tzinfo is not None:
                    expiry_naive = expiry_naive.astimezone(dt_timezone.utc).replace(tzinfo=None)
                    creds.expiry = expiry_naive

                # Refresh if expired or expires within 5 minutes
                if expiry_naive <= now_utc_naive or (expiry_naive - now_utc_naive).total_seconds() < 300:
                    needs_refresh = True
            else:
                # No expiry info, refresh to get it
                needs_refresh = True
            
            if needs_refresh:
                logger.debug(f"Refreshing token for user {user.id}")
                creds.refresh(_TimeoutRequest())
                logger.info(f"Token refreshed successfully for user {user.id}")
                # Save refreshed tokens back to database
                _save_user_credentials(user, creds)
            else:
                logger.debug(f"Token for user {user.id} is still valid, no refresh needed")
                
        except Exception as e:
            logger.error(f"Token refresh failed for user {user.id}: {e}")
            # Check if we can continue with existing token
            if creds.expiry:
                now_utc_naive = datetime.now(dt_timezone.utc).replace(tzinfo=None)
                expiry_naive = creds.expiry
                if expiry_naive.tzinfo is not None:
                    expiry_naive = expiry_naive.astimezone(dt_timezone.utc).replace(tzinfo=None)
                    creds.expiry = expiry_naive

                if expiry_naive <= now_utc_naive:
                    raise GoogleAuthError(
                        f"Token expired and refresh failed: {e}. "
                        "Please log out and log in again to re-authorize."
                    )
            # Token might still be valid, continue
            logger.warning("Attempting to use existing token anyway...")
    else:
        logger.warning(f"No refresh token available for user {user.id}, using access token as-is")
    
    # Build and return the service (with timeout-protected HTTP transport)
    try:
        service_name, version = service_configs[service_type]
        authed_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=GOOGLE_API_TIMEOUT))
        service = build(service_name, version, http=authed_http)
        logger.info(f"Google {service_type} service created successfully for user {user.id}")
        
        # Store in cache if provided
        if cache is not None:
            cache_key = f"{user.id}:{service_type}"
            cache[cache_key] = service
            logger.debug(f"Cached Google {service_type} service for user {user.id}")
        
        return service
        
    except Exception as e:
        logger.error(f"Failed to create Google {service_type} service for user {user.id}: {e}")
        raise GoogleAuthError(f"Failed to create Google {service_type} service: {e}")


def _get_user_credentials(user) -> Optional[Credentials]:
    """
    Load user's Google OAuth credentials from UserSecret.
    
    Args:
        user: Django User instance
        
    Returns:
        Optional[Credentials]: Google Credentials object or None if not found
    """
    try:
        from src.accounts.models import UserSecret
        
        user_secret = UserSecret.objects.filter(user=user).first()
        
        if not user_secret:
            logger.warning(f"No UserSecret found for user {user.id}")
            return None
        
        if not user_secret.encrypted_google_access_token:
            logger.warning(f"No Google access token found for user {user.id}")
            return None
        
        # Decrypt tokens
        access_token = decrypt_value(user_secret.encrypted_google_access_token)
        
        if not access_token:
            logger.error(f"Failed to decrypt access token for user {user.id}")
            return None
        
        refresh_token = None
        if user_secret.encrypted_google_refresh_token:
            refresh_token = decrypt_value(user_secret.encrypted_google_refresh_token)
        
        token_expiry = None
        if user_secret.encrypted_google_token_expiry:
            expiry_str = decrypt_value(user_secret.encrypted_google_token_expiry)
            if expiry_str:
                try:
                    expiry_dt = datetime.fromisoformat(expiry_str)
                    if expiry_dt.tzinfo is not None:
                        expiry_dt = expiry_dt.astimezone(dt_timezone.utc).replace(tzinfo=None)
                    token_expiry = expiry_dt
                except ValueError:
                    logger.warning(f"Invalid token expiry format for user {user.id}")
        
        # Get client info from settings
        client_config = get_google_client_config()
        client_id = client_config['client_id']
        client_secret = client_config['client_secret']
        
        # Use user's granted scopes for refresh (required for refresh to succeed).
        # Passing config.scopes can cause invalid_scope if the refresh token was
        # issued with a different scope set.
        granted_scopes = user_secret.get_scopes_list()
        scopes_for_creds = granted_scopes if granted_scopes else config.scopes
        
        # Create credentials object
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes_for_creds,
        )
        
        # Set expiry if available (Google auth expects naive UTC datetime)
        if token_expiry:
            try:
                creds.expiry = token_expiry
            except Exception as e:
                logger.warning(f"Could not set expiry for user {user.id}: {e}, will refresh to get fresh expiry")
        
        logger.debug(f"Successfully loaded credentials for user {user.id}")
        return creds
        
    except Exception as e:
        logger.error(f"Error loading credentials for user {user.id}: {e}")
        return None


def _save_user_credentials(user, creds: Credentials):
    """
    Save refreshed Google OAuth credentials back to UserSecret.
    
    Args:
        user: Django User instance
        creds: Google Credentials object with refreshed tokens
    """
    try:
        from src.accounts.models import UserSecret
        
        user_secret = UserSecret.objects.filter(user=user).first()
        
        if not user_secret:
            logger.error(f"No UserSecret found for user {user.id}, cannot save credentials")
            return
        
        # Encrypt and store new access token
        if creds.token:
            user_secret.encrypted_google_access_token = encrypt_value(creds.token)
        
        # Update refresh token if available
        if creds.refresh_token:
            user_secret.encrypted_google_refresh_token = encrypt_value(creds.refresh_token)
        
        # Update expiry if available (store as naive UTC)
        if creds.expiry:
            expiry_to_store = creds.expiry
            if expiry_to_store.tzinfo is not None:
                expiry_to_store = expiry_to_store.astimezone(dt_timezone.utc).replace(tzinfo=None)
            user_secret.encrypted_google_token_expiry = encrypt_value(expiry_to_store.isoformat())
        
        user_secret.save()
        logger.debug(f"Saved refreshed credentials for user {user.id}")
        
    except Exception as e:
        logger.error(f"Error saving credentials for user {user.id}: {e}")


def has_valid_google_credentials(user) -> bool:
    """
    Check if user has valid Google OAuth credentials.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user has valid credentials, False otherwise
    """
    try:
        creds = _get_user_credentials(user)
        return creds is not None
    except Exception:
        return False


def verify_drive_permissions(user) -> bool:
    """
    Verify that user has Google Drive permissions.
    
    Args:
        user: Django User instance
        
    Returns:
        bool: True if user has Drive scope, False otherwise
    """
    try:
        from src.accounts.models import UserSecret
        
        user_secret = UserSecret.objects.filter(user=user).first()
        if not user_secret:
            logger.warning(f"No UserSecret found for user {user.id}")
            return False
        
        return user_secret.has_drive_permission()
        
    except Exception as e:
        logger.error(f"Error verifying Drive permissions for user {user.id}: {e}")
        return False


def verify_gmail_permissions(user) -> bool:
    """
    Verify that user has Gmail permissions.

    Args:
        user: Django User instance

    Returns:
        bool: True if user has Gmail scope, False otherwise
    """
    try:
        from src.accounts.models import UserSecret

        user_secret = UserSecret.objects.filter(user=user).first()
        if not user_secret:
            logger.warning(f"No UserSecret found for user {user.id}")
            return False

        return user_secret.has_gmail_permission()

    except Exception as e:
        logger.error(f"Error verifying Gmail permissions for user {user.id}: {e}")
        return False


def get_missing_scopes(user) -> list:
    """
    Get list of missing required scopes for the user.
    
    Args:
        user: Django User instance
        
    Returns:
        list: List of missing required scope URLs
    """
    try:
        from src.accounts.models import UserSecret
        
        user_secret = UserSecret.objects.filter(user=user).first()
        if not user_secret:
            # If no UserSecret, all required scopes are missing
            return DEFAULT_REQUIRED_SCOPES
        
        return user_secret.get_missing_scopes(DEFAULT_REQUIRED_SCOPES)
        
    except Exception as e:
        logger.error(f"Error getting missing scopes for user {user.id}: {e}")
        return DEFAULT_REQUIRED_SCOPES
