"""
Google OAuth Views

Handles Google OAuth authentication flow including:
- Initiating OAuth authorization
- Processing OAuth callback
- Handling account linking with password confirmation
- Separate consent flow for non-Google users needing Google services
"""

from django.conf import settings
from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth import login
from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator

from src.common.google_account.auth import (
    create_authorization_url,
    exchange_code_for_tokens,
    get_google_user_info,
    store_user_tokens,
    GoogleAuthError,
)
from src.common.google_account.config import FULL_SCOPES, SERVICE_SCOPES
from src.common.logging_utils.logging_config import get_logger
from .models import CustomUser, UserSecret

logger = get_logger('google_oauth')


# ============================================================================
# GOOGLE LOGIN VIEWS
# ============================================================================

class GoogleLoginView(View):
    """
    Initiates Google OAuth login flow.
    
    GET /accounts/google/login/
    
    Redirects user to Google's consent screen with full scopes
    (login + Gmail, Drive, Calendar).
    """
    
    def get(self, request):
        """Start OAuth flow by redirecting to Google."""
        # Don't allow if already logged in
        if request.user.is_authenticated:
            messages.info(request, _('You are already logged in.'))
            return redirect(settings.LOGIN_REDIRECT_URL)
        
        try:
            # Create authorization URL with full scopes
            auth_url, state = create_authorization_url(
                request=request,
                scopes=FULL_SCOPES,
            )
            
            # Store state in session for CSRF protection
            request.session['oauth_state'] = state
            request.session['oauth_flow'] = 'login'  # Mark this as login flow
            
            logger.debug(f"Redirecting to Google OAuth, state={state[:8]}...")
            
            return redirect(auth_url)
            
        except GoogleAuthError as e:
            logger.error(f"Failed to create authorization URL: {e}")
            messages.error(request, _('Unable to connect to Google. Please try again.'))
            return redirect('accounts:login')


class GoogleCallbackView(View):
    """
    Handles OAuth callback from Google.
    
    GET /accounts/google/callback/
    
    Processes the authorization code and handles:
    - Connect flow (oauth_flow == 'connect'): store tokens for current user, redirect to profile
    - Login flow: new user registration, existing Google user login,
      account linking for existing email/password users
    """
    
    def get(self, request):
        """Process OAuth callback from Google."""
        # Check for errors from Google
        error = request.GET.get('error')
        if error:
            error_desc = request.GET.get('error_description', error)
            logger.warning(f"Google OAuth error: {error} - {error_desc}")
            messages.error(request, _('Google sign-in was cancelled or failed.'))
            return redirect('accounts:login')
        
        # Verify state parameter (CSRF protection)
        state = request.GET.get('state')
        stored_state = request.session.get('oauth_state')
        
        if not state or state != stored_state:
            logger.warning("OAuth state mismatch - possible CSRF attack")
            messages.error(request, _('Invalid authentication request. Please try again.'))
            return redirect('accounts:login')
        
        # Get authorization code
        code = request.GET.get('code')
        if not code:
            logger.warning("No authorization code in callback")
            messages.error(request, _('Invalid authentication response. Please try again.'))
            return redirect('accounts:login')
        
        # Connect flow: existing user adding Google services; same redirect URI, branch on session
        if request.session.get('oauth_flow') == 'connect':
            if not request.user.is_authenticated or request.session.get('oauth_user_id') != request.user.id:
                for key in ('oauth_state', 'oauth_flow', 'oauth_user_id'):
                    request.session.pop(key, None)
                messages.error(request, _('Session invalid. Please try again.'))
                return redirect('accounts:login' if not request.user.is_authenticated else 'accounts:profile')
            try:
                tokens = exchange_code_for_tokens(code, request)
                access_token = tokens.get('access_token')
                refresh_token = tokens.get('refresh_token')
                expires_in = tokens.get('expires_in')
                scopes = tokens.get('scope', '').split(' ')
                if not access_token:
                    raise GoogleAuthError("No access token in response")
                store_user_tokens(
                    request.user,
                    access_token,
                    refresh_token,
                    expires_in,
                    scopes,
                )
                logger.info(f"User {request.user.id} connected Google services")
                messages.success(request, _('Google services connected successfully!'))
            except GoogleAuthError as e:
                logger.error(f"Google OAuth error (connect): {e}")
                messages.error(request, _('Failed to connect Google services. Please try again.'))
            finally:
                for key in ('oauth_state', 'oauth_flow', 'oauth_user_id'):
                    request.session.pop(key, None)
            return redirect('accounts:profile')
        
        try:
            # Exchange code for tokens
            tokens = exchange_code_for_tokens(code, request)
            
            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')
            expires_in = tokens.get('expires_in')
            scopes = tokens.get('scope', '').split(' ')
            
            if not access_token:
                raise GoogleAuthError("No access token in response")
            
            # Fetch user info from Google
            user_info = get_google_user_info(access_token)
            
            google_email = user_info.get('email')
            if not google_email:
                raise GoogleAuthError("No email in user info")
            
            google_email = google_email.lower()  # Normalize email
            email_verified = user_info.get('email_verified', False)
            
            if not email_verified:
                logger.warning(f"Unverified email from Google: {google_email}")
                messages.error(request, _('Please verify your Google account email first.'))
                return redirect('accounts:login')
            
            # Check if user exists
            existing_user = CustomUser.objects.filter(email=google_email).first()
            
            if existing_user:
                # User exists - check if they have a password set
                if existing_user.has_usable_password() and not existing_user.is_google_account:
                    # Traditional user trying to use Google login
                    # Store OAuth data in session and redirect to link confirmation
                    request.session['google_link_data'] = {
                        'email': google_email,
                        'access_token': access_token,
                        'refresh_token': refresh_token,
                        'expires_in': expires_in,
                        'scopes': scopes,
                        'user_info': user_info,
                    }
                    return redirect('accounts:google_link_confirm')
                else:
                    # Google user or user without password - just log in
                    self._update_and_login_user(
                        request, existing_user, 
                        access_token, refresh_token, expires_in, scopes, user_info
                    )
                    messages.success(request, _('Welcome back!'))
                    return redirect(settings.LOGIN_REDIRECT_URL)
            else:
                # New user - create account
                user = self._create_google_user(user_info)
                store_user_tokens(user, access_token, refresh_token, expires_in, scopes)
                
                login(request, user)
                logger.info(f"New Google user registered and logged in: {user.email}")
                
                messages.success(request, _('Account created successfully! Welcome!'))
                return redirect(settings.LOGIN_REDIRECT_URL)
                
        except GoogleAuthError as e:
            logger.error(f"Google OAuth error: {e}")
            messages.error(request, _('Google sign-in failed. Please try again.'))
            return redirect('accounts:login')
            
        finally:
            # Clean up session
            request.session.pop('oauth_state', None)
    
    def _create_google_user(self, user_info: dict) -> CustomUser:
        """
        Create a new user from Google user info.
        
        Args:
            user_info: Dict with Google user info
            
        Returns:
            CustomUser: The newly created user
        """
        email = user_info.get('email', '').lower()
        
        user = CustomUser.objects.create_user(
            email=email,
            password=None,  # No password for Google users
        )
        
        # Set user unusable password
        user.set_unusable_password()
        
        # Populate from Google info
        user.first_name = user_info.get('given_name', '')
        user.last_name = user_info.get('family_name', '')
        user.is_email_verified = True  # Google verifies emails
        user.is_google_account = True
        
        # Handle profile picture URL (could download and save, but URL is simpler)
        # user.profile_picture = user_info.get('picture')  # Would need to download
        
        user.save()
        
        logger.info(f"Created new Google user: {email}")
        return user
    
    def _update_and_login_user(
        self, request, user, 
        access_token, refresh_token, expires_in, scopes, user_info
    ):
        """Update user tokens and log them in."""
        # Store tokens
        store_user_tokens(user, access_token, refresh_token, expires_in, scopes)
        
        # Optionally update user info from Google
        if not user.first_name and user_info.get('given_name'):
            user.first_name = user_info['given_name']
        if not user.last_name and user_info.get('family_name'):
            user.last_name = user_info['family_name']
        user.save()
        
        # Log in
        login(request, user)
        logger.info(f"Google user logged in: {user.email}")


class GoogleLinkConfirmView(View):
    """
    Handles account linking confirmation for existing email/password users.
    
    GET /accounts/google/link-confirm/
    POST /accounts/google/link-confirm/
    
    When a user with an existing email/password account tries to log in
    with Google, they are redirected here to confirm their password
    before linking the accounts.
    """
    
    template_name = 'accounts/google_link_confirm.html'
    
    def get(self, request):
        """Display password confirmation form."""
        # Check if we have pending link data
        link_data = request.session.get('google_link_data')
        if not link_data:
            messages.error(request, _('No pending account link. Please try again.'))
            return redirect('accounts:login')
        
        context = {
            'email': link_data['email'],
        }
        return render(request, self.template_name, context)
    
    def post(self, request):
        """Process password confirmation and link accounts."""
        link_data = request.session.get('google_link_data')
        if not link_data:
            messages.error(request, _('No pending account link. Please try again.'))
            return redirect('accounts:login')
        
        password = request.POST.get('password', '')
        email = link_data['email']
        
        # Find user and verify password
        user = CustomUser.objects.filter(email=email).first()
        
        if not user:
            messages.error(request, _('Account not found. Please try again.'))
            request.session.pop('google_link_data', None)
            return redirect('accounts:login')
        
        if not user.check_password(password):
            messages.error(request, _('Incorrect password. Please try again.'))
            return render(request, self.template_name, {'email': email})
        
        # Password verified - link accounts
        try:
            # Store Google tokens
            store_user_tokens(
                user,
                link_data['access_token'],
                link_data['refresh_token'],
                link_data['expires_in'],
                link_data['scopes'],
            )
            
            # Update user flags (keep is_google_account as False since they have a password)
            # This allows them to use both login methods
            user_info = link_data.get('user_info', {})
            if not user.first_name and user_info.get('given_name'):
                user.first_name = user_info['given_name']
            if not user.last_name and user_info.get('family_name'):
                user.last_name = user_info['family_name']
            user.is_email_verified = True  # Google verified the email
            user.save()
            
            # Clean up session
            request.session.pop('google_link_data', None)
            
            # Log in
            login(request, user)
            logger.info(f"Linked Google account for user: {user.email}")
            
            messages.success(
                request, 
                _('Your Google account has been linked successfully!')
            )
            return redirect(settings.LOGIN_REDIRECT_URL)
            
        except Exception as e:
            logger.error(f"Error linking Google account: {e}")
            messages.error(request, _('Failed to link account. Please try again.'))
            return redirect('accounts:login')


# ============================================================================
# GOOGLE CONNECT VIEWS (for existing users needing Google services)
# ============================================================================

@method_decorator(login_required, name='dispatch')
class GoogleConnectView(View):
    """
    Initiates Google OAuth for existing users who need Google services.
    
    GET /accounts/google/connect/
    
    This is for users who registered with email/password but now need
    access to Google Calendar, Gmail, or Drive features.
    """
    
    def get(self, request):
        """Start OAuth flow for connecting Google services."""
        try:
            # Check if user already has Google connected
            try:
                user_secret = request.user.secrets
                if user_secret.encrypted_google_access_token:
                    # Already connected - check if they need more scopes
                    granted_scopes = set(user_secret.get_scopes_list())
                    needed_scopes = set(SERVICE_SCOPES)
                    
                    if needed_scopes.issubset(granted_scopes):
                        messages.info(request, _('Your Google account is already connected.'))
                        return redirect('accounts:profile')
            except UserSecret.DoesNotExist:
                pass
            
            # Create authorization URL with service scopes
            # Include login scopes too for the consent screen
            auth_url, state = create_authorization_url(
                request=request,
                scopes=FULL_SCOPES,
                login_hint=request.user.email,  # Pre-fill email
            )
            
            # Store state in session
            request.session['oauth_state'] = state
            request.session['oauth_flow'] = 'connect'  # Mark as connect flow
            request.session['oauth_user_id'] = request.user.id
            
            logger.debug(f"User {request.user.id} connecting Google services")
            
            return redirect(auth_url)
            
        except GoogleAuthError as e:
            logger.error(f"Failed to create authorization URL: {e}")
            messages.error(request, _('Unable to connect to Google. Please try again.'))
            return redirect('accounts:profile')


@method_decorator(login_required, name='dispatch')
class GoogleConnectCallbackView(View):
    """
    OAuth callback for Google services connection (separate redirect URI).
    
    GET /accounts/google/connect/callback/
    
    Currently unused: connect flow uses the same redirect URI as login and is
    handled in GoogleCallbackView (branch on oauth_flow == 'connect'). If you
    later register a separate connect callback URL in Google Console, route it here.
    """
    
    def get(self, request):
        """Process OAuth callback for service connection."""
        # Verify this is a connect flow
        if request.session.get('oauth_flow') != 'connect':
            return redirect('accounts:google_callback')
        
        # Verify user matches
        stored_user_id = request.session.get('oauth_user_id')
        if stored_user_id != request.user.id:
            messages.error(request, _('Session mismatch. Please try again.'))
            return redirect('accounts:profile')
        
        # Check for errors
        error = request.GET.get('error')
        if error:
            error_desc = request.GET.get('error_description', error)
            logger.warning(f"Google OAuth error: {error} - {error_desc}")
            messages.error(request, _('Google connection was cancelled.'))
            return redirect('accounts:profile')
        
        # Verify state
        state = request.GET.get('state')
        stored_state = request.session.get('oauth_state')
        
        if not state or state != stored_state:
            messages.error(request, _('Invalid request. Please try again.'))
            return redirect('accounts:profile')
        
        code = request.GET.get('code')
        if not code:
            messages.error(request, _('Invalid response. Please try again.'))
            return redirect('accounts:profile')
        
        try:
            # Exchange code for tokens
            tokens = exchange_code_for_tokens(code, request)
            
            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')
            expires_in = tokens.get('expires_in')
            scopes = tokens.get('scope', '').split(' ')
            
            if not access_token:
                raise GoogleAuthError("No access token in response")
            
            # Store tokens for current user
            store_user_tokens(
                request.user,
                access_token,
                refresh_token,
                expires_in,
                scopes,
            )
            
            logger.info(f"User {request.user.id} connected Google services")
            messages.success(request, _('Google services connected successfully!'))
            
        except GoogleAuthError as e:
            logger.error(f"Google connection error: {e}")
            messages.error(request, _('Failed to connect Google services. Please try again.'))
        
        finally:
            # Clean up session
            request.session.pop('oauth_state', None)
            request.session.pop('oauth_flow', None)
            request.session.pop('oauth_user_id', None)
        
        return redirect('accounts:profile')


# ============================================================================
# GOOGLE DISCONNECT VIEW
# ============================================================================

@method_decorator(login_required, name='dispatch')
class GoogleDisconnectView(View):
    """
    Disconnects Google services from user's account.
    
    POST /accounts/google/disconnect/
    
    Note: Google-only users (no password) cannot disconnect.
    """
    
    def post(self, request):
        """Disconnect Google from user account."""
        user = request.user
        
        # Prevent Google-only users from disconnecting
        if user.is_google_account and not user.has_usable_password():
            messages.error(
                request, 
                _('You cannot disconnect Google because it is your only login method.')
            )
            return redirect('accounts:profile')
        
        try:
            from src.common.google_account.auth import revoke_user_tokens
            
            if revoke_user_tokens(user):
                messages.success(request, _('Google account disconnected.'))
                logger.info(f"User {user.id} disconnected Google")
            else:
                messages.warning(request, _('Google account may not have been fully disconnected.'))
                
        except Exception as e:
            logger.error(f"Error disconnecting Google for user {user.id}: {e}")
            messages.error(request, _('Failed to disconnect Google. Please try again.'))
        
        return redirect('accounts:profile')
