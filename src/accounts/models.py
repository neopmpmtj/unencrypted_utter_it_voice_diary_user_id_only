from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _
import json

# ============================================================================
# CUSTOM USER MANAGER
# ============================================================================
# The manager handles creating users and superusers
# We override it to use email instead of username

class CustomUserManager(BaseUserManager):
    """
    Custom manager for CustomUser model.
    Handles creation of users with email instead of username.
    """
    
    def create_user(self, email, password=None, **extra_fields):
        """
        Create and save a regular user.
        
        Args:
            email (str): User's email address (will be normalized to lowercase)
            password (str): User's password (will be hashed by Django)
            **extra_fields: Any additional fields (first_name, last_name, etc)
            
        Returns:
            CustomUser: The created user instance
            
        Raises:
            ValueError: If email is not provided
        """
        # Email is required - we raise error if not provided
        if not email:
            raise ValueError(_('Email address is required'))
        
        # Normalize email: converts to lowercase and removes whitespace
        # This ensures 'Test@Email.com' and 'test@email.com' are the same
        email = self.normalize_email(email)
        
        # Create user instance (not saved yet)
        user = self.model(email=email, **extra_fields)
        
        # SECURITY: set_password hashes the password using PBKDF2
        # Never store passwords in plaintext
        # set_password handles all hashing internally
        user.set_password(password)
        
        # Save to database
        user.save(using=self._db)
        
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        """
        Create and save a superuser (admin).
        
        Args:
            email (str): Superuser's email
            password (str): Superuser's password
            **extra_fields: Additional fields
            
        Returns:
            CustomUser: The created superuser instance
        """
        # Superusers must have these permissions set
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        
        # Validate that superuser flags are set correctly
        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True'))
        
        # Use create_user method to create the superuser
        return self.create_user(email, password, **extra_fields)


# ============================================================================
# CUSTOM USER MODEL
# ============================================================================
# This is the main user model - replaces Django's default User

class CustomUser(AbstractUser):
    """
    Custom user model using email for authentication instead of username.
    
    Inherits from AbstractUser, which provides:
    - password (auto-hashed)
    - is_active (bool, defaults True)
    - is_staff (bool, for admin access)
    - is_superuser (bool, for all permissions)
    - date_joined (timestamp, auto-set)
    - last_login (timestamp, auto-set)
    - first_name, last_name (optional)
    - groups, user_permissions (for granular permissions)
    
    We customize:
    - Remove username requirement
    - Make email the unique identifier
    - Add optional profile picture
    - Add email verification flag
    """
    
    # ---- Remove default username ----
    # AbstractUser has username by default, we don't need it
    # Set to None to remove it from the model
    username = None
    
    # ---- Email field (our identifier) ----
    # unique=True ensures no two users have same email
    # max_length=255 is standard for email fields
    email = models.EmailField(
        _('email address'),
        unique=True,
        max_length=255,
        help_text=_('User must provide a valid, unique email address.')
    )
    
    # ---- Optional profile fields ----
    # blank=True, null=True = optional fields
    profile_picture = models.ImageField(
        upload_to='profile_pictures/',
        blank=True,
        null=True,
        help_text=_('User profile picture (optional)')
    )
    
    # Email verification flag
    # Used to track if user has verified their email
    is_email_verified = models.BooleanField(
        default=False,
        help_text=_('Whether user has verified their email address')
    )
    
    # Email verification token (for security)
    # Stores a unique token sent via email for verification
    email_verification_token = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        help_text=_('Token for email verification')
    )
    
    # Google OAuth registration flag
    # True if user registered via Google OAuth (may not have a password)
    is_google_account = models.BooleanField(
        default=False,
        help_text=_('Whether user registered via Google OAuth')
    )

    # Account deletion (soft delete with 30-day grace period)
    deletion_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('When user requested account deletion; null if not requested')
    )

    # Subscription / plan tier (used for rate limits e.g. transcription)
    TIER_CHOICES = [
        ('free', _('Free')),
        ('pro', _('Pro')),
        ('ultra', _('Ultra')),
    ]
    tier = models.CharField(
        max_length=10,
        choices=TIER_CHOICES,
        default='free',
        help_text=_('Plan tier (free / pro / ultra); affects rate limits such as transcription.')
    )

    # Test user flag -- bypasses all quota and feature restrictions.
    # Set manually via admin panel or direct SQL. When set to False,
    # standard tier rules apply immediately.
    is_test_user = models.BooleanField(
        default=False,
        help_text=_('Test user: bypasses all quota and feature restrictions. '
                    'Set manually via admin or DB.')
    )

    # App admin flag -- no quotas, no rate limits, no usage card.
    # Set manually via admin or DB.
    is_app_admin = models.BooleanField(
        default=False,
        help_text=_('App admin: no quotas, no rate limits, no usage card. Set via admin or DB.')
    )

    # Creation timestamp (auto-set by AbstractUser)
    # We don't need to define this, but explaining it:
    # date_joined = DateTimeField inherited from AbstractUser
    
    # ---- Manager ----
    # Tell Django to use our custom manager
    # This handles user creation via CustomUser.objects.create_user()
    objects = CustomUserManager()
    
    # ---- Authentication field ----
    # THIS IS CRITICAL: Tell Django to use email for login, not username
    USERNAME_FIELD = 'email'
    
    # Fields required when creating superuser via createsuperuser command
    REQUIRED_FIELDS = []  # Empty because email is in USERNAME_FIELD
    
    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        db_table = 'accounts_customuser'  # Explicit table name
    
    def __str__(self):
        """String representation of user (shown in admin)"""
        return self.email
    
    def get_full_name(self):
        """Return user's full name or email if name not set"""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.first_name:
            return self.first_name
        return self.email
    
    def get_short_name(self):
        """Return user's short name"""
        return self.first_name or self.email.split('@')[0]


# ============================================================================
# OPTIONAL: USER PROFILE MODEL
# ============================================================================
# For additional user data not in the main User model
# This follows the single-responsibility principle

class UserProfile(models.Model):
    """
    Extended user profile for additional information.
    Uses OneToOne relationship with CustomUser.
    """
    # One user = one profile, bidirectional relationship
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,  # Delete profile if user is deleted
        related_name='profile',
        help_text=_('The user this profile belongs to')
    )
    
    # Bio/About section
    bio = models.TextField(
        max_length=500,
        blank=True,
        help_text=_('User bio (optional)')
    )
    
    # Phone number
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text=_('User phone number (optional)')
    )
    
    # Location
    location = models.CharField(
        max_length=100,
        blank=True,
        help_text=_('User location (optional)')
    )
    
    # Social links
    website = models.URLField(
        blank=True,
        help_text=_('User website (optional)')
    )
    
    # Profile creation & update timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('user profile')
        verbose_name_plural = _('user profiles')
        db_table = 'accounts_userprofile'
    
    def __str__(self):
        return f"Profile of {self.user.email}"


# ============================================================================
# EXPLANATION OF SIGNALS (OPTIONAL)
# ============================================================================
# You might want to auto-create UserProfile when CustomUser is created
# Add this at the end of models.py:

from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    """
    Signal: When a CustomUser is created, auto-create UserProfile.
    
    This is optional but good practice - keeps user data organized.
    """
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
    """Signal: When CustomUser is saved, save the profile too."""
    if hasattr(instance, 'profile'):
        instance.profile.save()


# ============================================================================
# USER SECRET MODEL - Google OAuth Token Storage
# ============================================================================

class UserSecret(models.Model):
    """
    Stores encrypted Google OAuth tokens for users.
    
    This model is used to store:
    - Access tokens (encrypted) - for API calls
    - Refresh tokens (encrypted) - for getting new access tokens
    - Token expiry (encrypted) - to know when to refresh
    - Granted scopes - to verify user has necessary permissions
    
    OAuth tokens are encrypted at rest using Fernet(MASTER_ENCRYPTION_KEY).
    """
    
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='secrets',
        help_text=_('The user this secret belongs to')
    )
    
    # Encrypted OAuth tokens
    encrypted_google_access_token = models.TextField(
        blank=True,
        null=True,
        help_text=_('Encrypted Google OAuth access token')
    )
    
    encrypted_google_refresh_token = models.TextField(
        blank=True,
        null=True,
        help_text=_('Encrypted Google OAuth refresh token')
    )
    
    encrypted_google_token_expiry = models.TextField(
        blank=True,
        null=True,
        help_text=_('Encrypted token expiry timestamp (ISO format)')
    )
    
    # Store granted scopes as JSON array string
    google_token_scopes = models.TextField(
        blank=True,
        null=True,
        help_text=_('JSON array of granted Google OAuth scopes')
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('user secret')
        verbose_name_plural = _('user secrets')
        db_table = 'accounts_usersecret'
    
    def __str__(self):
        return f"Secrets for {self.user.email}"
    
    def get_scopes_list(self) -> list:
        """
        Get the granted scopes as a Python list.
        
        Returns:
            list: List of granted scope URLs, or empty list if none
        """
        if not self.google_token_scopes:
            return []
        try:
            return json.loads(self.google_token_scopes)
        except json.JSONDecodeError:
            return []
    
    def set_scopes_list(self, scopes: list):
        """
        Set the granted scopes from a Python list.
        
        Args:
            scopes: List of scope URLs to store
        """
        self.google_token_scopes = json.dumps(scopes) if scopes else None
    
    def has_required_scopes(self, required_scopes: list) -> bool:
        """
        Check if user has all required scopes.
        
        Args:
            required_scopes: List of scope URLs that must be present
            
        Returns:
            bool: True if all required scopes are present
        """
        granted = set(self.get_scopes_list())
        required = set(required_scopes)
        return required.issubset(granted)
    
    def get_missing_scopes(self, required_scopes: list) -> list:
        """
        Get list of missing required scopes.
        
        Args:
            required_scopes: List of scope URLs that should be present
            
        Returns:
            list: List of scope URLs that are missing
        """
        granted = set(self.get_scopes_list())
        required = set(required_scopes)
        return list(required - granted)
    
    def has_drive_permission(self) -> bool:
        """Check if user has Google Drive scope."""
        return 'https://www.googleapis.com/auth/drive' in self.get_scopes_list()
    
    def has_gmail_permission(self) -> bool:
        """Check if user has Gmail scope."""
        return 'https://www.googleapis.com/auth/gmail.modify' in self.get_scopes_list()
    
    def has_calendar_permission(self) -> bool:
        """Check if user has Calendar scope (full or events-only)."""
        scopes = self.get_scopes_list()
        return (
            'https://www.googleapis.com/auth/calendar' in scopes
            or 'https://www.googleapis.com/auth/calendar.events' in scopes
        )


# ============================================================================
# USER PREFERENCES MODEL - Voice Diary Settings
# ============================================================================

class UserPreferences(models.Model):
    """
    User-specific preferences for the Voice Diary application.
    
    Stores settings like preferred language for translation and
    audio retention period for automatic cleanup.
    """
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='preferences',
        help_text=_('The user these preferences belong to')
    )
    
    preferred_language = models.CharField(
        max_length=50,
        default='en',
        help_text=_('Preferred language for translations (ISO 639-1 code)')
    )
    
    audio_retention_days = models.PositiveIntegerField(
        default=7,
        help_text=_('Days to keep original audio files (0 = delete immediately after processing)')
    )
    
    enable_translation = models.BooleanField(
        default=True,
        help_text=_('If True, translate diary content to preferred language when detected language differs')
    )
    
    onboarding_completed = models.BooleanField(
        default=False,
        help_text=_('Whether user has completed initial onboarding')
    )
    
    # Theme preferences
    ACCENT_THEME_CHOICES = [
        ('green', _('Green')),
        ('blue', _('Blue')),
        ('indigo', _('Indigo')),
        ('purple', _('Purple')),
        ('red', _('Red')),
        ('orange', _('Orange')),
        ('yellow', _('Yellow')),
    ]

    dark_mode = models.BooleanField(
        default=True,
        help_text=_('Whether dark mode is enabled')
    )

    accent_theme = models.CharField(
        max_length=10,
        choices=ACCENT_THEME_CHOICES,
        default='green',
        help_text=_('Selected accent color theme')
    )

    standalone_app_ui = models.BooleanField(
        default=True,
        help_text=_('If True, use app-like mode (hide browser address bar) when added to home screen')
    )

    show_recording_timer = models.BooleanField(
        default=True,
        help_text=_('Whether to show the recording duration timer during recording')
    )

    TRANSCRIPTION_SIZE_CHOICES = [
        ('small', _('Small')),
        ('medium', _('Medium')),
        ('large', _('Large')),
    ]

    transcription_text_size = models.CharField(
        max_length=10,
        choices=TRANSCRIPTION_SIZE_CHOICES,
        default='small',
        help_text=_('Font size for transcriptions and diary entries (small=14px, medium=16px, large=18px)')
    )

    interface_language = models.CharField(
        max_length=10,
        default='pt-pt',
        help_text=_('Interface language for viewing pages (e.g. pt-pt, en). Persists across browser clears.')
    )

    drive_attachment_folder_name = models.CharField(
        max_length=255,
        default='VoiceDiaryFiles/attachments',
        blank=True,
        help_text=_('Google Drive folder path for attachments (e.g. VoiceDiaryFiles/attachments). Files are uploaded into this folder.')
    )
    drive_attachment_folder_id = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text=_('Google Drive folder ID of the leaf folder; set when folder is first created or resolved.')
    )

    TIMEZONE_CHOICES = [
        # Europe
        ('Europe/Lisbon', _('Lisbon (Portugal, UTC+0/+1)')),
        ('Europe/London', _('London (UK, UTC+0/+1)')),
        ('Europe/Paris', _('Paris (France, UTC+1/+2)')),
        ('Europe/Berlin', _('Berlin (Germany, UTC+1/+2)')),
        ('Europe/Madrid', _('Madrid (Spain, UTC+1/+2)')),
        ('Europe/Rome', _('Rome (Italy, UTC+1/+2)')),
        ('Europe/Amsterdam', _('Amsterdam (Netherlands, UTC+1/+2)')),
        ('Europe/Brussels', _('Brussels (Belgium, UTC+1/+2)')),
        ('Europe/Vienna', _('Vienna (Austria, UTC+1/+2)')),
        ('Europe/Zurich', _('Zurich (Switzerland, UTC+1/+2)')),
        ('Europe/Stockholm', _('Stockholm (Sweden, UTC+1/+2)')),
        ('Europe/Oslo', _('Oslo (Norway, UTC+1/+2)')),
        ('Europe/Copenhagen', _('Copenhagen (Denmark, UTC+1/+2)')),
        ('Europe/Helsinki', _('Helsinki (Finland, UTC+2/+3)')),
        ('Europe/Warsaw', _('Warsaw (Poland, UTC+1/+2)')),
        ('Europe/Prague', _('Prague (Czech Republic, UTC+1/+2)')),
        ('Europe/Budapest', _('Budapest (Hungary, UTC+1/+2)')),
        ('Europe/Bucharest', _('Bucharest (Romania, UTC+2/+3)')),
        ('Europe/Athens', _('Athens (Greece, UTC+2/+3)')),
        ('Europe/Istanbul', _('Istanbul (Turkey, UTC+3)')),
        ('Europe/Moscow', _('Moscow (Russia, UTC+3)')),
        # Americas
        ('America/New_York', _('New York (UTC-5/-4)')),
        ('America/Chicago', _('Chicago (UTC-6/-5)')),
        ('America/Denver', _('Denver (UTC-7/-6)')),
        ('America/Los_Angeles', _('Los Angeles (UTC-8/-7)')),
        ('America/Toronto', _('Toronto (UTC-5/-4)')),
        ('America/Vancouver', _('Vancouver (UTC-8/-7)')),
        ('America/Sao_Paulo', _('São Paulo (UTC-3)')),
        ('America/Buenos_Aires', _('Buenos Aires (UTC-3)')),
        ('America/Mexico_City', _('Mexico City (UTC-6/-5)')),
        # Asia / Pacific
        ('Asia/Dubai', _('Dubai (UTC+4)')),
        ('Asia/Kolkata', _('Mumbai/Kolkata (UTC+5:30)')),
        ('Asia/Singapore', _('Singapore (UTC+8)')),
        ('Asia/Tokyo', _('Tokyo (UTC+9)')),
        ('Asia/Seoul', _('Seoul (UTC+9)')),
        ('Asia/Shanghai', _('Shanghai (UTC+8)')),
        ('Asia/Hong_Kong', _('Hong Kong (UTC+8)')),
        ('Asia/Jakarta', _('Jakarta (UTC+7)')),
        ('Australia/Sydney', _('Sydney (UTC+10/+11)')),
        ('Pacific/Auckland', _('Auckland (UTC+12/+13)')),
        # Africa
        ('Africa/Johannesburg', _('Johannesburg (UTC+2)')),
        ('Africa/Lagos', _('Lagos (UTC+1)')),
        ('Africa/Cairo', _('Cairo (UTC+2)')),
        ('Africa/Nairobi', _('Nairobi (UTC+3)')),
        # UTC
        ('UTC', _('UTC')),
    ]

    timezone = models.CharField(
        max_length=50,
        choices=TIMEZONE_CHOICES,
        default='Europe/Lisbon',
        help_text=_('User timezone, used for calendar event scheduling.')
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('user preferences')
        verbose_name_plural = _('user preferences')
        db_table = 'accounts_userpreferences'
    
    def __str__(self):
        return f"Preferences for {self.user.email}"


# ============================================================================
# GLOBAL SETTINGS MODEL - Admin-editable Configuration
# ============================================================================

class GlobalSettings(models.Model):
    """
    Admin-editable configuration overrides stored in database.
    
    Allows administrators to change application settings without
    code deployment. Settings are keyed by a dotted path (e.g.,
    'recorder.max_duration') and can store any JSON-serializable value.
    """
    key = models.CharField(
        max_length=100,
        unique=True,
        help_text=_('Setting key (e.g., recorder.max_duration)')
    )
    
    value = models.JSONField(
        help_text=_('Setting value (any JSON type)')
    )
    
    description = models.TextField(
        blank=True,
        help_text=_('Description of this setting')
    )
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('global setting')
        verbose_name_plural = _('global settings')
        db_table = 'accounts_globalsettings'
    
    def __str__(self):
        return f"{self.key} = {self.value}"
    
    @classmethod
    def get_value(cls, key: str, default=None):
        """
        Get a setting value by key.
        
        Args:
            key: The setting key (e.g., 'recorder.max_duration')
            default: Default value if setting doesn't exist
            
        Returns:
            The setting value or default
        """
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default
    
    @classmethod
    def set_value(cls, key: str, value, description: str = ''):
        """
        Set a setting value by key.
        
        Args:
            key: The setting key
            value: The value to set (must be JSON-serializable)
            description: Optional description
        """
        obj, created = cls.objects.update_or_create(
            key=key,
            defaults={'value': value, 'description': description}
        )
        return obj


# ============================================================================
# API USAGE LOG MODEL - Track API Usage Per User
# ============================================================================

class APIUsageLog(models.Model):
    """
    Track API usage per user for cost monitoring.
    
    Records usage of external APIs like OpenAI Whisper and GPT
    to help users and administrators understand costs.
    """
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='api_usage_logs',
        help_text=_('The user who triggered this API call')
    )
    
    service = models.CharField(
        max_length=50,
        help_text=_('Service name: whisper, gpt-4o-mini, etc.')
    )
    
    usage_type = models.CharField(
        max_length=50,
        help_text=_('Usage type: audio_minutes, input_tokens, output_tokens')
    )
    
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text=_('Usage amount')
    )
    
    ingest_item = models.ForeignKey(
        'ingestion.IngestItem',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='api_usage_logs',
        help_text=_('Related ingest item (if applicable)')
    )
    
    origin = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=_('Originating function name, e.g. process_audio_ingest')
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = _('API usage log')
        verbose_name_plural = _('API usage logs')
        db_table = 'accounts_apiusagelog'
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['service', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.service} - {self.usage_type}: {self.amount}"


class UserFeatureConfig(models.Model):
    """
    Per-user feature toggles and settings.
    Replaces the old TenantFeatureConfig (one-to-one with Tenant).
    """
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="feature_config",
    )

    enable_auto_classification = models.BooleanField(
        default=True,
        help_text=_("Automatically classify items after processing"),
    )
    enable_calendar_integration = models.BooleanField(
        default=True,
        help_text=_("Enable automatic Google Calendar event creation"),
    )
    calendar_trigger_tags = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Tag names that trigger calendar parsing"),
    )
    default_calendar_id = models.CharField(
        max_length=255,
        default="primary",
        help_text=_("Google Calendar ID for event creation"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("user feature config")
        verbose_name_plural = _("user feature configs")
        db_table = "accounts_userfeatureconfig"

    def __str__(self) -> str:
        return f"Config for {self.user.email}"

    def get_calendar_trigger_tags(self) -> list:
        if self.calendar_trigger_tags:
            return self.calendar_trigger_tags
        return ["calendário"]

    @classmethod
    def get_for_user(cls, user) -> "UserFeatureConfig":
        config, _created = cls.objects.get_or_create(user=user)
        return config


# Auto-create UserPreferences when CustomUser is created
@receiver(post_save, sender=CustomUser)
def create_user_preferences(sender, instance, created, **kwargs):
    """Signal: When a CustomUser is created, auto-create UserPreferences."""
    if created:
        UserPreferences.objects.get_or_create(user=instance)
