from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from .models import CustomUser, UserProfile, UserPreferences, GlobalSettings, APIUsageLog

# ============================================================================
# CUSTOM USER ADMIN
# ============================================================================


class UserProfileInline(admin.StackedInline):
    """Inline admin for editing UserProfile on the CustomUser page."""
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'profile'
    fk_name = 'user'
    readonly_fields = ('created_at', 'updated_at')
    max_num = 1


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    """
    Admin interface for CustomUser model.
    Customized from Django's default UserAdmin to work with email-based auth.
    """
    
    # Fields displayed in list view
    list_display = ['email', 'first_name', 'last_name', 'tier', 'is_test_user', 'is_app_admin', 'is_email_verified', 'is_active', 'date_joined']
    
    # Fields you can filter by on the right side
    list_filter = ['is_active', 'is_staff', 'is_superuser', 'is_email_verified', 'tier', 'is_test_user', 'is_app_admin', 'date_joined']
    
    # Fields shown when editing a user
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'profile_picture')}),
        (_('Plan'), {'fields': ('tier',)}),
        (_('Testing'), {'fields': ('is_test_user',)}),
        (_('App Admin'), {'fields': ('is_app_admin',)}),
        (_('Email verification'), {'fields': ('is_email_verified', 'email_verification_token')}),
        (_('Permissions'), {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
            'classes': ('collapse',),
        }),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    
    # Fields shown when creating a new user
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )
    
    # Search by email instead of username
    search_fields = ['email', 'first_name', 'last_name']
    
    # Order by email
    ordering = ['email']
    
    # Show the UserProfile on the CustomUser change page
    inlines = (UserProfileInline,)

    def get_inline_instances(self, request, obj=None):
        """Don't show inlines when creating a new user (obj is None)."""
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin interface for UserProfile model."""
    
    list_display = ['user', 'location', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__email', 'location']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    """Admin interface for UserPreferences model."""
    
    list_display = ['user', 'preferred_language', 'enable_translation', 'audio_retention_days', 'updated_at']
    list_filter = ['preferred_language', 'enable_translation', 'audio_retention_days']
    search_fields = ['user__email']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(GlobalSettings)
class GlobalSettingsAdmin(admin.ModelAdmin):
    """Admin interface for GlobalSettings model."""

    list_display = ['key', 'value', 'updated_at']
    search_fields = ['key', 'description']
    readonly_fields = ['updated_at']
    ordering = ['key']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.key.startswith('llm.'):
            try:
                from src.common.model_picker import reload_llm_config
                reload_llm_config()
            except Exception:
                pass

    def delete_model(self, request, obj):
        key = obj.key
        super().delete_model(request, obj)
        if key.startswith('llm.'):
            try:
                from src.common.model_picker import reload_llm_config
                reload_llm_config()
            except Exception:
                pass

    def delete_queryset(self, request, queryset):
        keys = list(queryset.values_list('key', flat=True))
        super().delete_queryset(request, queryset)
        if any(k.startswith('llm.') for k in keys):
            try:
                from src.common.model_picker import reload_llm_config
                reload_llm_config()
            except Exception:
                pass


@admin.register(APIUsageLog)
class APIUsageLogAdmin(admin.ModelAdmin):
    """Admin interface for APIUsageLog model."""
    
    list_display = ['user', 'service', 'usage_type', 'amount', 'origin', 'created_at']
    list_filter = ['service', 'usage_type', 'origin', 'created_at']
    search_fields = ['user__email', 'service']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
