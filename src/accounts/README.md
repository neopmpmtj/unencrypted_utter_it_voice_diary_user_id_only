# Accounts App

Django app for email-based authentication (no username), optional Google OAuth, user profiles, and account deletion with a 30-day grace period.

The app is mounted at `/src.accounts/` (see project [src/utter_it/urls.py](../utter_it/urls.py)); all account URLs live under that prefix with namespace `accounts`.

---

## Models

### CustomUser

([src/accounts/models.py](models.py)) Extends `AbstractUser` with `username = None` and `USERNAME_FIELD = 'email'`. Uses `CustomUserManager` for `create_user` and `create_superuser`.

- **Custom fields**: `email` (unique), `profile_picture`, `is_email_verified`, `email_verification_token`, `is_google_account`, `deletion_requested_at`
- **Inherited**: `first_name`, `last_name`, `password`, `is_active`, `is_staff`, `is_superuser`, `date_joined`, `last_login`, `groups`, `user_permissions`

### UserProfile

OneToOne to `CustomUser` (`related_name='profile'`). Fields: `bio`, `phone_number`, `location`, `website`, `created_at`, `updated_at`. Created automatically by a `post_save` signal when a `CustomUser` is created.

### UserSecret

OneToOne to `CustomUser` (`related_name='secrets'`). Stores encrypted Google OAuth tokens (access, refresh, expiry) and granted scopes (JSON). Used by [src/common/google_account/auth.py](../common/google_account/auth.py); encryption is done via [src/common/utils/encryption.py](../common/utils/encryption.py) (per-user key from `user_id` and `SECRET_KEY`).

Helper methods: `get_scopes_list`, `set_scopes_list`, `has_required_scopes`, `get_missing_scopes`, `has_drive_permission`, `has_gmail_permission`, `has_calendar_permission`.

---

## Views and URLs

- **URL config**: [src/accounts/urls.py](urls.py) (app_name `accounts`). Google routes in [src/accounts/google_urls.py](google_urls.py) are included under `google/`.

### Registration and authentication

- **RegisterView** – registration form; creates user, sends verification email, redirects to login.
- **VerifyEmailView** – GET `verify-email/<token>/`; marks email verified and clears token.
- **LoginView** – email + password; handles Google-only users (hint to use “Sign in with Google”) and rate limiting (`login_attempt_limiter` by IP).
- **logout_view** – POST only; clears session and redirects to login.

### Profile

- **ProfileView** – `login_required`; shows and updates user + profile via `UserInfoForm` and `UserProfileForm`.

### Password

- **PasswordResetView**, **PasswordResetDoneView**, **PasswordResetConfirmView**, **PasswordResetCompleteView** – reset flow with custom forms; rate limited by `password_reset_limiter`.
- **PasswordChangeView**, **PasswordChangeDoneView** – change password when logged in.
- **resend_verification_view** – POST; resend verification email; rate limited by `resend_verification_limiter`.

### Account deletion

- **RequestAccountDeletionView** – requests deletion; password confirmation for non-Google users; Google-only users skip. Sends email with cancel link (30-day validity).
- **account_delete_done_view** – confirmation page after requesting deletion.
- **cancel_account_deletion_view** – GET with signed token; cancels deletion (token valid 30 days).

### API

- **check_email_availability** – GET; query param `email`; JSON response with `available` (true/false).

### Google OAuth

([src/accounts/google_views.py](google_views.py), [src/accounts/google_urls.py](google_urls.py))

- **GoogleLoginView** – starts OAuth login (redirect to Google).
- **GoogleCallbackView** – handles callback; supports login flow and connect flow (session `oauth_flow`); new user creation, existing Google user login, or link-confirm for existing email user.
- **GoogleLinkConfirmView** – password confirmation to link an existing email/password account with Google.
- **GoogleConnectView** – starts OAuth for an already logged-in user to connect Google services.
- **GoogleConnectCallbackView** – callback for connect flow (same redirect URI, branch on session).
- **GoogleDisconnectView** – POST; revokes and clears Google tokens (Google-only users cannot disconnect).

---

## Forms

([src/accounts/forms.py](forms.py))

| Form | Purpose |
|------|---------|
| CustomUserCreationForm | Registration: email, first_name, last_name, password1, password2 |
| CustomAuthenticationForm | Login: email, password, remember_me; validates credentials and `is_email_verified` |
| CustomPasswordResetForm | Request password reset by email |
| CustomSetPasswordForm | Set new password (reset confirm) |
| CustomPasswordChangeForm | Change password when logged in (current + new) |
| UserProfileForm | Edit profile: bio, phone_number, location, website |
| UserInfoForm | Edit user: first_name, last_name, profile_picture |
| AccountDeletionForm | Password confirmation for account deletion (non-Google users) |

---

## Google OAuth integration

- **Common layer**: [src/common/google_account/auth.py](../common/google_account/auth.py) (e.g. `create_authorization_url`, `exchange_code_for_tokens`, `get_google_user_info`, `store_user_tokens`, `revoke_user_tokens`, `get_authenticated_service`), [src/common/google_account/config.py](../common/google_account/config.py) (`LOGIN_SCOPES`, `SERVICE_SCOPES`, `FULL_SCOPES`, OAuth endpoints). Tokens are stored in `UserSecret` and encrypted with [src/common/utils/encryption.py](../common/utils/encryption.py) (`encrypt_value` / `decrypt_value`).
- **Settings**: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`; optional `GOOGLE_OAUTH_REDIRECT_URI` (otherwise built from request). See [src/utter_it/settings/base.py](../utter_it/settings/base.py).

---

## Rate limiting

([src/common/utils/rate_limiter.py](../common/utils/rate_limiter.py)) Uses `IdentifierRateLimiter` by IP:

| Limiter | Limit | Used in |
|---------|-------|---------|
| login_attempt_limiter | 10 per hour | LoginView |
| password_reset_limiter | 5 per hour | PasswordResetView |
| resend_verification_limiter | 3 per hour | resend_verification_view |

---

## Management command

**delete_expired_accounts** ([src/accounts/management/commands/delete_expired_accounts.py](management/commands/delete_expired_accounts.py))

Permanently deletes `CustomUser` rows where `deletion_requested_at` is older than the configurable retention period.

- **Usage**: `python manage.py delete_expired_accounts`
- **Option**: `--dry-run` – report what would be deleted without deleting.
- **Config**: Uses `GlobalSettings` key `accounts.deletion_retention_days` (default 90).
- Also runs daily via Celery beat task `delete_expired_accounts_task`.

---

## Admin

([src/accounts/admin.py](admin.py))

- **CustomUserAdmin** – email-based user admin with **UserProfileInline**.
- **UserProfileAdmin** – standalone profile admin.

`UserSecret` is not registered in admin.

---

## Dependencies and settings

- **Django**: `AUTH_USER_MODEL = 'accounts.CustomUser'`, `LOGIN_URL = 'accounts:login'`, `LOGIN_REDIRECT_URL`, `LOGOUT_REDIRECT_URL`. Email backend (e.g. SMTP) and `DEFAULT_FROM_EMAIL` for verification and password reset. Cache backend required for rate limiters.
- **Local**: `src.common` (google_account, utils.encryption, utils.rate_limiter, logging_utils).

---

## Templates

Templates live under `templates/accounts/` (project-level):

- **Auth**: `register.html`, `login.html`
- **Verification**: link in email points to verify-email view
- **Profile**: `profile.html`
- **Password**: `password_reset_form.html`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`, `password_reset_email.html`, `password_reset_subject.txt`, `password_change.html`, `password_change_done.html`
- **Account deletion**: `account_delete.html`, `account_delete_done.html`
- **Google**: `google_link_confirm.html`, `google_error.html`

Base layout is inherited from the project (`templates/base.html`).

---

## Account deletion configuration (GlobalSettings)

Admin-editable keys for account deletion:

| Key | Default | Purpose |
|-----|---------|---------|
| `accounts.deletion_grace_days` | 30 | Days user can cancel via email link |
| `accounts.deletion_retention_days` | 90 | Days before permanent deletion |

See [deletion_config.py](deletion_config.py) for helpers.
