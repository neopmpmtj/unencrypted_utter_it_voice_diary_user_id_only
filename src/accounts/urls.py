from django.urls import path, include
from . import views

app_name = 'accounts'

urlpatterns = [
    # Registration & Authentication
    path('register/', views.RegisterView.as_view(), name='register'),
    path('verify-email/<str:token>/', views.VerifyEmailView.as_view(), name='verify_email'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Onboarding
    path('onboarding/', views.OnboardingView.as_view(), name='onboarding'),
    
    # Google OAuth
    path('google/', include('src.accounts.google_urls')),
    
    # Profile
    path('profile/', views.ProfileView.as_view(), name='profile'),
    
    # Password Management
    path('password-reset/', views.PasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path(
        'password-reset-confirm/<uidb64>/<token>/',
        views.PasswordResetConfirmView.as_view(),
        name='password_reset_confirm',
    ),
    path('password-reset-complete/', views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('password-change/', views.PasswordChangeView.as_view(), name='password_change'),
    path('password-change/done/', views.PasswordChangeDoneView.as_view(), name='password_change_done'),
    path('resend-verification/', views.resend_verification_view, name='resend_verification'),
    path('account-delete/', views.RequestAccountDeletionView.as_view(), name='account_delete'),
    path('account-delete/done/', views.account_delete_done_view, name='account_delete_done'),
    path(
        'account-delete-cancel/<path:token>/',
        views.cancel_account_deletion_view,
        name='account_delete_cancel',
    ),
    
    # API endpoints
    path('api/check-email/', views.check_email_availability, name='check_email'),
    path('api/theme/', views.update_theme_preferences, name='update_theme'),
    path('set-interface-language/', views.set_interface_language, name='set_interface_language'),
]
