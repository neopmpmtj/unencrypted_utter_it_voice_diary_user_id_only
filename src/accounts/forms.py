from django import forms
from django.contrib.auth.forms import (
    UserCreationForm, UserChangeForm, PasswordResetForm, SetPasswordForm,
    PasswordChangeForm,
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import CustomUser, UserProfile, UserPreferences


# ============================================================================
# REGISTRATION FORM
# ============================================================================

class CustomUserCreationForm(UserCreationForm):
    """
    Form for user registration.
    
    Inherits from Django's UserCreationForm but:
    - Uses email instead of username
    - Includes password validation
    - Includes CSRF protection (automatic)
    - Includes optional profile fields
    """
    
    # Email field - explicit definition
    email = forms.EmailField(
        label=_('Email Address'),
        max_length=254,
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'your.email@example.com',
            'autocomplete': 'email',
        })
    )
    
    # First name (optional)
    first_name = forms.CharField(
        label=_('First Name'),
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'John',
        })
    )
    
    # Last name (optional)
    last_name = forms.CharField(
        label=_('Last Name'),
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Doe',
        })
    )
    
    # Password field - shown explicitly
    password1 = forms.CharField(
        label=_('Password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'At least 8 characters',
            'autocomplete': 'new-password',
        }),
        help_text=_('Must be at least 8 characters'),
    )
    
    # Password confirmation - must match password1
    password2 = forms.CharField(
        label=_('Confirm Password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Re-enter password',
            'autocomplete': 'new-password',
        })
    )
    
    class Meta:
        model = CustomUser
        fields = ['email', 'first_name', 'last_name', 'password1', 'password2']
    
    def clean_email(self):
        """
        Validate email address.
        Check if email already exists and if format is valid.
        """
        email = self.cleaned_data.get('email')
        
        # Email field validator runs first (format check)
        if email:
            # Check if email already registered
            if CustomUser.objects.filter(email=email).exists():
                raise ValidationError(
                    _('An account with this email already exists.'),
                    code='email_exists'
                )
        
        return email
    
    def clean_password2(self):
        """
        Validate password confirmation.
        Ensure password1 and password2 match.
        """
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        
        if password1 and password2:
            if password1 != password2:
                raise ValidationError(
                    _('Passwords do not match.'),
                    code='password_mismatch'
                )
        
        return password2
    
    def clean(self):
        """Full form validation"""
        cleaned_data = super().clean()
        return cleaned_data
    
    def save(self, commit=True):
        """
        Save user to database.
        Password is automatically hashed by set_password (in manager).
        """
        user = super().save(commit=False)
        
        # Email is already set by form data
        # Password is set by parent class
        
        if commit:
            user.save()
            # Create user profile automatically
            UserProfile.objects.get_or_create(user=user)
        
        return user


# ============================================================================
# LOGIN FORM
# ============================================================================

class CustomAuthenticationForm(forms.Form):
    """
    Form for user login (email + password).
    
    Custom form instead of using built-in because:
    - We authenticate by email, not username
    - We want custom error messages
    - We can add rate limiting later
    """
    
    email = forms.EmailField(
        label=_('Email Address'),
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'your.email@example.com',
            'autocomplete': 'email',
            'autofocus': True,
        })
    )
    
    password = forms.CharField(
        label=_('Password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password',
        })
    )
    
    # Optional: Remember me checkbox (not storing in DB)
    remember_me = forms.BooleanField(
        required=False,
        label=_('Remember me'),
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
        })
    )
    
    def clean(self):
        """
        Validate login credentials.
        This doesn't log in the user - that happens in the view.
        """
        email = self.cleaned_data.get('email')
        password = self.cleaned_data.get('password')
        
        # Check both fields provided
        if not email or not password:
            raise ValidationError(
                _('Email and password are required.'),
                code='incomplete'
            )
        
        # Try to find user and check password
        if email and password:
            try:
                user = CustomUser.objects.get(email=email)
                
                # Use check_password to compare plaintext with hash
                # NEVER compare passwords directly!
                if not user.check_password(password):
                    raise ValidationError(
                        _('Invalid email or password.'),
                        code='invalid_login'
                    )
                
                # Optional: Check if email is verified
                if not user.is_email_verified:
                    raise ValidationError(
                        _('Please verify your email before logging in.'),
                        code='email_not_verified'
                    )
                
                # Optional: Check if account is active
                if not user.is_active:
                    raise ValidationError(
                        _('This account is inactive.'),
                        code='account_inactive'
                    )
                
            except CustomUser.DoesNotExist:
                raise ValidationError(
                    _('Invalid email or password.'),
                    code='invalid_login'
                )
        
        return self.cleaned_data


# ============================================================================
# PASSWORD RESET FORM
# ============================================================================

class CustomPasswordResetForm(PasswordResetForm):
    """
    Form for initiating password reset.
    User enters email, gets reset link via email.
    Does not reveal whether email exists (security).
    """
    
    email = forms.EmailField(
        label=_('Email Address'),
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'your.email@example.com',
            'autocomplete': 'email',
            'autofocus': True,
        })
    )


# ============================================================================
# SET PASSWORD FORM (for password reset confirm)
# ============================================================================

# ============================================================================
# PASSWORD CHANGE FORM (logged-in user)
# ============================================================================

class CustomPasswordChangeForm(PasswordChangeForm):
    """Form for changing password when logged in (requires current password)."""

    old_password = forms.CharField(
        label=_('Current password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter current password',
            'autocomplete': 'current-password',
        }),
    )
    new_password1 = forms.CharField(
        label=_('New password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter new password',
            'autocomplete': 'new-password',
        }),
    )
    new_password2 = forms.CharField(
        label=_('Confirm new password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm new password',
            'autocomplete': 'new-password',
        }),
    )


# ============================================================================
# SET PASSWORD FORM (for password reset confirm)
# ============================================================================

class CustomSetPasswordForm(SetPasswordForm):
    """Form for setting new password during password reset confirm."""

    new_password1 = forms.CharField(
        label=_('New password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter new password',
            'autocomplete': 'new-password',
        }),
    )
    new_password2 = forms.CharField(
        label=_('Confirm new password'),
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm new password',
            'autocomplete': 'new-password',
        }),
    )


# ============================================================================
# USER PROFILE FORM
# ============================================================================

class UserProfileForm(forms.ModelForm):
    """Form for editing user profile information."""
    
    class Meta:
        model = UserProfile
        fields = ['bio', 'phone_number', 'location', 'website']
        widgets = {
            'bio': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Tell us about yourself...',
            }),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '+1 (555) 000-0000',
                'type': 'tel',
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'City, Country',
            }),
            'website': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://example.com',
                'type': 'url',
            }),
        }


# ============================================================================
# ACCOUNT DELETION FORM
# ============================================================================

class AccountDeletionForm(forms.Form):
    """Form for confirming account deletion by typing email (GitHub-style)."""

    confirmation_email = forms.CharField(
        label=_('Type your email to confirm'),
        strip=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': '',
            'autocomplete': 'email',
        }),
    )

    def __init__(self, *args, expected_email='', **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_email = expected_email

    def clean_confirmation_email(self):
        value = self.cleaned_data.get('confirmation_email', '').strip()
        if value.lower() != (self.expected_email or '').lower():
            raise forms.ValidationError(_('The email does not match your account.'))
        return value


# ============================================================================
# USER INFO FORM
# ============================================================================

class UserInfoForm(forms.ModelForm):
    """Form for editing user basic information (not password)."""
    
    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'profile_picture']
        widgets = {
            'first_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'First name',
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Last name',
            }),
            'profile_picture': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*',
            }),
        }


# ============================================================================
# USER PREFERENCES FORM (Voice diary)
# ============================================================================

PREFERRED_LANGUAGE_CHOICES = [
    ('pt-pt', _('Portuguese (Portugal)')),
    ('en', _('English')),
]


class UserPreferencesForm(forms.ModelForm):
    """Form for Voice diary preferences: storage language and audio retention."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.initial.get('preferred_language') or getattr(self.instance, 'preferred_language', '')
        normalized = self._normalize_language_code(current)
        if normalized:
            self.initial['preferred_language'] = normalized

    preferred_language = forms.ChoiceField(
        choices=PREFERRED_LANGUAGE_CHOICES,
        label=_('Store transcriptions in'),
        help_text=_('Language diary entries are stored in. If you speak another language, it will be translated to this one.'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    enable_translation = forms.BooleanField(
        required=False,
        label=_('Translate diary content'),
        help_text=_('When enabled, entries in another language are translated to your stored language.'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    
    standalone_app_ui = forms.BooleanField(
        required=False,
        label=_('App-like mode (hide browser address bar)'),
        help_text=_('When enabled, hides the browser toolbar when using the site from Add to Home Screen.'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    show_recording_timer = forms.BooleanField(
        required=False,
        label=_('Show recording timer'),
        help_text=_('Display elapsed recording time below the record button'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    show_inline_rewrite = forms.BooleanField(
        required=False,
        label=_('Show rewrite on voice and text input'),
        help_text=_('When enabled, shows the Rewrite control next to the record button (voice) and next to Save (text).'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    drive_attachment_folder_name = forms.CharField(
        required=False,
        max_length=255,
        label=_('Google Drive folder for attachments'),
        help_text=_('Folder path in Drive for uploaded files (e.g. VoiceDiaryFiles/attachments). Default: VoiceDiaryFiles/attachments'),
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'VoiceDiaryFiles/attachments'}),
    )

    timezone = forms.ChoiceField(
        choices=UserPreferences.TIMEZONE_CHOICES,
        label=_('Timezone'),
        help_text=_('Used for calendar event scheduling.'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = UserPreferences
        fields = ['preferred_language', 'enable_translation', 'standalone_app_ui', 'show_recording_timer', 'show_inline_rewrite', 'drive_attachment_folder_name', 'timezone']

    def clean_preferred_language(self):
        value = self.cleaned_data.get('preferred_language', '')
        return self._normalize_language_code(value) or value

    @staticmethod
    def _normalize_language_code(lang_code: str) -> str:
        if not lang_code:
            return ''
        lowered = lang_code.lower()
        if lowered == 'pt-br':
            return 'pt-pt'
        if lowered in {'pt-pt', 'en'}:
            return lowered
        return lowered

    def save(self, commit=True):
        old_folder_name = (getattr(self.instance, 'drive_attachment_folder_name') or '').strip() if self.instance.pk else ''
        instance = super().save(commit=commit)
        if commit and instance.pk:
            new_folder_name = (self.cleaned_data.get('drive_attachment_folder_name') or '').strip()
            if old_folder_name != new_folder_name:
                instance.drive_attachment_folder_id = None
                instance.save(update_fields=['drive_attachment_folder_id'])
        return instance


class OnboardingLanguageForm(forms.Form):
    """Form for selecting preferred language during onboarding."""
    
    preferred_language = forms.ChoiceField(
        choices=PREFERRED_LANGUAGE_CHOICES,
        label=_('Select your preferred language'),
        help_text=_('Your diary entries will be stored in this language. If you speak another language, entries will be translated automatically.'),
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='pt-pt',  # Default to Portuguese (Portugal) for Portuguese audience
    )