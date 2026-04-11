from django.conf import settings
from django.shortcuts import render, redirect
from django.views import View
from django.views.decorators.http import require_http_methods
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    PasswordResetView as AuthPasswordResetView,
    PasswordResetDoneView as AuthPasswordResetDoneView,
    PasswordResetConfirmView as AuthPasswordResetConfirmView,
    PasswordResetCompleteView as AuthPasswordResetCompleteView,
    PasswordChangeView as AuthPasswordChangeView,
    PasswordChangeDoneView as AuthPasswordChangeDoneView,
)
from django.contrib import messages
from django.utils.decorators import method_decorator
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse, HttpResponse
from django.utils.translation import gettext_lazy as _

import secrets

from .models import CustomUser, UserPreferences, UserSecret
from .forms import (
    CustomUserCreationForm, CustomAuthenticationForm,
    CustomPasswordResetForm, CustomSetPasswordForm, CustomPasswordChangeForm,
    AccountDeletionForm, UserProfileForm, UserInfoForm, UserPreferencesForm,
    OnboardingLanguageForm,
)


# ============================================================================
# REGISTRATION VIEW
# ============================================================================

class RegisterView(View):
    """
    View for user registration.
    Handles GET (show form) and POST (process registration).
    """
    
    template_name = 'accounts/register.html'
    form_class = CustomUserCreationForm
    
    def get(self, request):
        """Display registration form"""
        if request.user.is_authenticated:
            return redirect(settings.LOGIN_REDIRECT_URL)
        
        form = self.form_class()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        """Process registration form submission"""
        form = self.form_class(request.POST)
        
        if form.is_valid():
            # User created but not logged in yet
            user = form.save(commit=False)
            
            # Optional: Set email_verified=False initially
            user.is_email_verified = False
            
            # Generate email verification token
            # This token is sent to user via email
            user.email_verification_token = self._generate_verification_token()
            
            user.save()
            
            try:
                _send_verification_email(request, user)
            except Exception:
                pass  # User created; show same message so we do not reveal mail failure
            
            messages.success(
                request,
                _('Registration successful. We sent a verification link to your email. '
                  'Please click it to verify your account before logging in.'),
                extra_tags='success'
            )
            
            return redirect('accounts:login')
        else:
            # Form has errors, show them
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
        
        return render(request, self.template_name, {'form': form})
    
    def _generate_verification_token(self):
        """Generate a secure random token for email verification"""
        return secrets.token_urlsafe(32)


def _send_verification_email(request, user):
    """Send verification email to user."""
    from django.core.mail import send_mail
    from django.urls import reverse
    from django.conf import settings

    verification_url = request.build_absolute_uri(
        reverse('accounts:verify_email', args=[user.email_verification_token])
    )

    send_mail(
        subject=_('Verify your email'),
        message=_('Click this link to verify: %(url)s') % {'url': verification_url},
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


# ============================================================================
# VERIFY EMAIL VIEW
# ============================================================================

class VerifyEmailView(View):
    """
    Handles email verification via link in verification email.
    GET /accounts/verify-email/<token>/
    """
    
    def get(self, request, token):
        user = CustomUser.objects.filter(email_verification_token=token).first()
        if not user:
            messages.error(
                request,
                _('Invalid or expired verification link. You can register again or request a new link.'),
            )
            return redirect('accounts:login')
        user.is_email_verified = True
        user.email_verification_token = None
        user.save()
        messages.success(
            request,
            _('Your email is verified. You can now log in.'),
        )
        return redirect('accounts:login')


# ============================================================================
# LOGIN VIEW
# ============================================================================

class LoginView(View):
    """
    View for user login.
    Authenticates user and creates session.
    
    Handles Google-only users by redirecting them to Google Sign-In.
    """
    
    template_name = 'accounts/login.html'
    form_class = CustomAuthenticationForm
    
    def get(self, request):
        """Display login form"""
        if request.user.is_authenticated:
            return redirect(settings.LOGIN_REDIRECT_URL)
        
        form = self.form_class()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        """Process login form"""
        form = self.form_class(request.POST)
        
        # Get email before full validation to check for Google-only users
        email = request.POST.get('email', '').strip().lower()
        
        if email:
            # Check if this is a Google-only user (no password set)
            try:
                existing_user = CustomUser.objects.get(email=email)
                
                # If user is a Google account and has no usable password
                if existing_user.is_google_account and not existing_user.has_usable_password():
                    messages.info(
                        request,
                        _('This account uses Google Sign-In. Please use the "Sign in with Google" button.'),
                        extra_tags='info'
                    )
                    return render(request, self.template_name, {
                        'form': form,
                        'show_google_hint': True,
                        'google_email': email,
                    })
            except CustomUser.DoesNotExist:
                pass  # User doesn't exist, proceed with normal validation
        
        show_resend_verification = False
        unverified_email = ''
        if not form.is_valid():
            from src.common.utils.rate_limiter import login_attempt_limiter
            ip = request.META.get('REMOTE_ADDR', 'unknown')
            allowed, info = login_attempt_limiter.check_rate_limit(ip)
            if not allowed:
                return HttpResponse("Too many login attempts.", status=429)
            for error_list in form.errors.as_data().values():
                for e in error_list:
                    if getattr(e, 'code', None) == 'email_not_verified':
                        show_resend_verification = True
                        unverified_email = email
                        break

        if form.is_valid():
            # Get credentials
            email = form.cleaned_data.get('email')
            password = form.cleaned_data.get('password')
            
            # Django's authenticate function:
            # 1. Finds user by username/email
            # 2. Checks password hash
            # Returns user if credentials valid, None otherwise
            user = authenticate(request, username=email, password=password)
            
            if user is not None:
                # Authentication successful
                login(request, user)  # Creates session
                
                # Optional: Handle "Remember me"
                if form.cleaned_data.get('remember_me'):
                    # Set session to persist for X days
                    request.session.set_expiry(30 * 24 * 60 * 60)  # 30 days
                
                messages.success(
                    request,
                    _('Login successful!'),
                    extra_tags='success'
                )
                
                # Redirect to next page or LOGIN_REDIRECT_URL
                next_url = request.GET.get('next', settings.LOGIN_REDIRECT_URL)
                return redirect(next_url)
            else:
                # Authentication failed
                messages.error(
                    request,
                    _('Invalid email or password.'),
                    extra_tags='error'
                )
        
        context = {'form': form}
        if show_resend_verification:
            context['show_resend_verification'] = True
            context['unverified_email'] = unverified_email
        return render(request, self.template_name, context)


# ============================================================================
# LOGOUT VIEW
# ============================================================================

@require_http_methods(['POST'])
def logout_view(request):
    """
    Handle user logout.
    Clears session and redirects.
    """
    logout(request)  # Deletes session data
    messages.success(request, _('You have been logged out.'))
    return redirect('accounts:login')


# ============================================================================
# PROFILE VIEW
# ============================================================================

@method_decorator(login_required, name='dispatch')
class ProfileView(View):
    """
    View for user profile/dashboard.
    Shows user info and allows editing.
    """
    
    template_name = 'accounts/profile.html'
    
    def get(self, request):
        """Display user profile"""
        prefs, created = UserPreferences.objects.get_or_create(user=request.user)
        user_secret = UserSecret.objects.filter(user=request.user).first()
        has_google = bool(user_secret and user_secret.encrypted_google_access_token)
        context = {
            'user': request.user,
            'user_info_form': UserInfoForm(instance=request.user),
            'user_profile_form': UserProfileForm(instance=request.user.profile),
            'user_preferences_form': UserPreferencesForm(instance=prefs),
            'has_google': has_google,
            'can_change_password': not (
                request.user.is_google_account and not request.user.has_usable_password()
            ),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        """Update user profile or Voice diary preferences."""
        if request.POST.get('save_preferences'):
            prefs, created = UserPreferences.objects.get_or_create(user=request.user)
            user_preferences_form = UserPreferencesForm(request.POST, instance=prefs)
            if user_preferences_form.is_valid():
                user_preferences_form.save()
                messages.success(request, _('Voice diary settings saved.'))
                return redirect(reverse('accounts:profile') + '#voice-diary')
            user_secret = UserSecret.objects.filter(user=request.user).first()
            has_google = bool(user_secret and user_secret.encrypted_google_access_token)
            context = {
                'user': request.user,
                'user_info_form': UserInfoForm(instance=request.user),
                'user_profile_form': UserProfileForm(instance=request.user.profile),
                'user_preferences_form': user_preferences_form,
                'has_google': has_google,
                'can_change_password': not (
                    request.user.is_google_account and not request.user.has_usable_password()
                ),
            }
            return render(request, self.template_name, context)

        user_info_form = UserInfoForm(request.POST, request.FILES, instance=request.user)
        user_profile_form = UserProfileForm(request.POST, instance=request.user.profile)

        if user_info_form.is_valid() and user_profile_form.is_valid():
            user_info_form.save()
            user_profile_form.save()
            messages.success(request, _('Profile updated successfully!'))
            return redirect('accounts:profile')

        prefs, created = UserPreferences.objects.get_or_create(user=request.user)
        user_secret = UserSecret.objects.filter(user=request.user).first()
        has_google = bool(user_secret and user_secret.encrypted_google_access_token)
        context = {
            'user': request.user,
            'user_info_form': user_info_form,
            'user_profile_form': user_profile_form,
            'user_preferences_form': UserPreferencesForm(instance=prefs),
            'has_google': has_google,
            'can_change_password': not (
                request.user.is_google_account and not request.user.has_usable_password()
            ),
        }
        return render(request, self.template_name, context)


# ============================================================================
# PASSWORD RESET VIEWS (Django built-in)
# ============================================================================

class PasswordResetView(AuthPasswordResetView):
    form_class = CustomPasswordResetForm
    template_name = 'accounts/password_reset_form.html'
    email_template_name = 'accounts/password_reset_email.html'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('accounts:password_reset_done')

    def post(self, request, *args, **kwargs):
        from src.common.utils.rate_limiter import password_reset_limiter
        ip = request.META.get('REMOTE_ADDR', 'unknown')
        allowed, info = password_reset_limiter.check_rate_limit(ip)
        if not allowed:
            return HttpResponse("Too many password reset requests.", status=429)
        return super().post(request, *args, **kwargs)


class PasswordResetDoneView(AuthPasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'


class PasswordResetConfirmView(AuthPasswordResetConfirmView):
    form_class = CustomSetPasswordForm
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('accounts:password_reset_complete')


class PasswordResetCompleteView(AuthPasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'


# ============================================================================
# PASSWORD CHANGE (logged-in)
# ============================================================================

class PasswordChangeView(AuthPasswordChangeView):
    form_class = CustomPasswordChangeForm
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('accounts:password_change_done')

    def dispatch(self, request, *args, **kwargs):
        if (
            request.user.is_authenticated
            and request.user.is_google_account
            and not request.user.has_usable_password()
        ):
            messages.info(
                request,
                _('You sign in with Google; password changes are not available.'),
            )
            return redirect('accounts:profile')
        return super().dispatch(request, *args, **kwargs)


class PasswordChangeDoneView(AuthPasswordChangeDoneView):
    template_name = 'accounts/password_change_done.html'


# ============================================================================
# RESEND VERIFICATION EMAIL
# ============================================================================

@require_http_methods(['POST'])
def resend_verification_view(request):
    """
    Resend verification email for unverified users.
    Only shown on login page when email_not_verified error occurs.
    Does not reveal whether email exists or is already verified.
    """
    from src.common.utils.rate_limiter import resend_verification_limiter

    ip = request.META.get('REMOTE_ADDR', 'unknown')
    allowed, info = resend_verification_limiter.check_rate_limit(ip)
    if not allowed:
        return HttpResponse("Too many requests.", status=429)

    email = request.POST.get('email', '').strip().lower()
    if not email:
        messages.success(
            request,
            _('If an account exists with that email and is not verified, a new verification link has been sent.'),
        )
        return redirect('accounts:login')

    try:
        user = CustomUser.objects.get(email=email)
        if user.is_email_verified:
            messages.info(request, _('This email is already verified. You can sign in.'))
            return redirect('accounts:login')
        user.email_verification_token = secrets.token_urlsafe(32)
        user.save()
        _send_verification_email(request, user)
    except CustomUser.DoesNotExist:
        pass
    except Exception:
        pass

    messages.success(
        request,
        _('If an account exists with that email and is not verified, a new verification link has been sent.'),
    )
    return redirect('accounts:login')


# ============================================================================
# ACCOUNT DELETION
# ============================================================================

@method_decorator(login_required, name='dispatch')
class RequestAccountDeletionView(View):
    """
    Request account deletion (soft delete).
    Requires typing email to confirm (GitHub-style).
    Sends email with cancel link (valid grace_days).
    """
    template_name = 'accounts/account_delete.html'
    form_class = AccountDeletionForm

    def get(self, request):
        from src.accounts.deletion_config import get_deletion_grace_days, get_deletion_retention_days

        user = request.user
        if user.deletion_requested_at:
            messages.warning(
                request,
                _('Account deletion is already scheduled. Check your email to cancel.'),
            )
            return redirect('accounts:profile')
        form = self.form_class(expected_email=user.email)
        return render(request, self.template_name, {
            'form': form,
            'masked_email': self._mask_email(user.email),
            'retention_days': get_deletion_retention_days(),
            'grace_days': get_deletion_grace_days(),
        })

    def post(self, request):
        from django.utils import timezone
        from django.core.mail import send_mail
        from django.conf import settings
        from django.urls import reverse
        from django.core.signing import TimestampSigner

        from src.accounts.deletion_config import get_deletion_grace_days, get_deletion_retention_days

        user = request.user
        if user.deletion_requested_at:
            return redirect('accounts:profile')

        form = self.form_class(request.POST, expected_email=user.email)
        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form,
                'masked_email': self._mask_email(user.email),
                'retention_days': get_deletion_retention_days(),
                'grace_days': get_deletion_grace_days(),
            })

        grace_days = get_deletion_grace_days()
        retention_days = get_deletion_retention_days()

        user.deletion_requested_at = timezone.now()
        user.is_active = False
        user.save()
        logout(request)

        signer = TimestampSigner()
        token = signer.sign(str(user.pk))
        cancel_url = request.build_absolute_uri(
            reverse('accounts:account_delete_cancel', args=[token])
        )
        try:
            send_mail(
                subject=_('Account deletion scheduled - Cancel within %(days)s days') % {'days': grace_days},
                message=_(
                    'You requested to delete your account. Your data will be retained for %(retention)s days before permanent deletion. You can cancel within %(grace)s days.\n\n'
                    'To cancel this and keep your account, click here:\n%(url)s\n\n'
                    'If you did not request this, you can ignore this email.'
                ) % {'url': cancel_url, 'retention': retention_days, 'grace': grace_days},
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            pass

        messages.success(
            request,
            _('Your account has been scheduled for deletion. Your data will be retained for %(retention)s days before permanent deletion. Check your email to cancel within %(grace)s days.') % {
                'retention': retention_days,
                'grace': grace_days,
            },
        )
        return redirect('accounts:account_delete_done')

    @staticmethod
    def _mask_email(email):
        """Mask email for display, e.g. u***@example.com"""
        if not email or '@' not in email:
            return '***@***.***'
        local, domain = email.split('@', 1)
        if len(local) <= 2:
            masked_local = '*' * len(local)
        else:
            masked_local = local[0] + '*' * (len(local) - 2) + local[-1]
        return f'{masked_local}@{domain}'


def account_delete_done_view(request):
    """Shown after user requests account deletion."""
    from src.accounts.deletion_config import get_deletion_retention_days, get_deletion_grace_days

    return render(request, 'accounts/account_delete_done.html', {
        'retention_days': get_deletion_retention_days(),
        'grace_days': get_deletion_grace_days(),
    })


def cancel_account_deletion_view(request, token):
    """
    Cancel account deletion via signed token from email.
    Valid for configurable grace_days.
    """
    from django.core.signing import TimestampSigner, SignatureExpired, BadSignature

    from src.accounts.deletion_config import get_deletion_grace_days

    grace_days = get_deletion_grace_days()
    max_age_seconds = grace_days * 24 * 60 * 60

    try:
        signer = TimestampSigner()
        user_pk = signer.unsign(token, max_age=max_age_seconds)
        user = CustomUser.objects.get(pk=user_pk)
    except (SignatureExpired, BadSignature, CustomUser.DoesNotExist, ValueError):
        messages.error(
            request,
            _('Invalid or expired link. Account deletion may have already completed.'),
        )
        return redirect('accounts:login')

    user.deletion_requested_at = None
    user.is_active = True
    user.save()
    messages.success(request, _('Account deletion cancelled. You can sign in again.'))
    return redirect('accounts:login')


# ============================================================================
# ONBOARDING VIEW
# ============================================================================

@method_decorator(login_required, name='dispatch')
class OnboardingView(View):
    """
    Onboarding view for new users to select their preferred language.
    
    Shown once after first login (both email/password and Google OAuth).
    """
    
    template_name = 'accounts/onboarding.html'
    form_class = OnboardingLanguageForm
    
    def get(self, request):
        """Display language selection form."""
        # If already completed onboarding, redirect to default landing
        if hasattr(request.user, 'preferences') and request.user.preferences.onboarding_completed:
            return redirect(settings.LOGIN_REDIRECT_URL)
        
        form = self.form_class()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        """Process language selection and mark onboarding as complete."""
        form = self.form_class(request.POST)
        
        if form.is_valid():
            # Get or create user preferences
            preferences, created = UserPreferences.objects.get_or_create(user=request.user)
            
            # Save language preference
            preferences.preferred_language = form.cleaned_data['preferred_language']
            preferences.onboarding_completed = True
            preferences.save()
            
            messages.success(
                request,
                _('Welcome! Your language preference has been saved.'),
                extra_tags='success'
            )
            return redirect(settings.LOGIN_REDIRECT_URL)
        
        return render(request, self.template_name, {'form': form})


# ============================================================================
# API ENDPOINTS (Optional)
# ============================================================================

def check_email_availability(request):
    """
    AJAX endpoint: Check if email is already registered.
    Returns JSON response.
    """
    email = request.GET.get('email', '')
    
    if email:
        exists = CustomUser.objects.filter(email=email).exists()
        return JsonResponse({'available': not exists})
    
    return JsonResponse({'error': _('No email provided')}, status=400)


@require_http_methods(["POST"])
@login_required
def update_theme_preferences(request):
    """
    AJAX endpoint: Update user's theme preferences (dark_mode, accent_theme).
    Called by the frontend theme.js when the user toggles dark mode or picks an accent.
    """
    import json
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': _('Invalid JSON')}, status=400)

    prefs = getattr(request.user, 'preferences', None)
    if prefs is None:
        from .models import UserPreferences
        prefs, _ignored = UserPreferences.objects.get_or_create(user=request.user)

    valid_accents = ['green', 'blue', 'indigo', 'purple', 'red', 'orange', 'yellow']

    update_fields = ['updated_at']

    if 'dark_mode' in data:
        prefs.dark_mode = bool(data['dark_mode'])
        update_fields.append('dark_mode')

    if 'accent_theme' in data and data['accent_theme'] in valid_accents:
        prefs.accent_theme = data['accent_theme']
        update_fields.append('accent_theme')

    if 'standalone_app_ui' in data:
        prefs.standalone_app_ui = bool(data['standalone_app_ui'])
        update_fields.append('standalone_app_ui')

    if 'show_recording_timer' in data:
        prefs.show_recording_timer = bool(data['show_recording_timer'])
        update_fields.append('show_recording_timer')

    if 'show_inline_rewrite' in data:
        prefs.show_inline_rewrite = bool(data['show_inline_rewrite'])
        update_fields.append('show_inline_rewrite')

    valid_sizes = ['small', 'medium', 'large']
    if 'transcription_text_size' in data and data['transcription_text_size'] in valid_sizes:
        prefs.transcription_text_size = data['transcription_text_size']
        update_fields.append('transcription_text_size')

    prefs.save(update_fields=update_fields)
    return JsonResponse({'ok': True})


@require_http_methods(["POST"])
def set_interface_language(request):
    """
    Set interface language. For authenticated users, saves to UserPreferences
    so it persists across browser clears. Also sets the cookie for consistency.
    """
    from django.utils import translation

    next_url = request.POST.get('next', request.GET.get('next', '/'))
    language = request.POST.get('language')

    valid_codes = [code for code, _ in settings.LANGUAGES]
    if not language or language not in valid_codes:
        return redirect(next_url)

    translation.activate(language)
    response = redirect(next_url)
    cookie_name = getattr(settings, 'LANGUAGE_COOKIE_NAME', 'django_language')
    response.set_cookie(
        cookie_name,
        language,
        max_age=365 * 24 * 60 * 60,
    )
    if request.user.is_authenticated:
        prefs, _ignored = UserPreferences.objects.get_or_create(user=request.user)
        prefs.interface_language = language
        prefs.save(update_fields=['interface_language', 'updated_at'])
    return response