# Accounts App – Test Suite

## How to run

From the project root (where `manage.py` lives):

```bash
python manage.py test src.accounts
```

Run a single test class:

```bash
python manage.py test src.accounts.tests.RegistrationTests
```

Run a single test method:

```bash
python manage.py test src.accounts.tests.RegistrationTests.test_user_registration
```

---

## Test classes and coverage

([src/accounts/tests.py](tests.py))

| Class | Coverage |
|-------|----------|
| **IdentifierRateLimiterTests** | First request allowed; requests up to max allowed; next request after max denied; window expiry allows again; reset_limit clears state. |
| **CustomUserModelTests** | create_user; create_superuser; unique email enforced. |
| **RegistrationTests** | Registration page loads; successful registration and redirect to login; duplicate email rejected; authenticated user redirected away from register. |
| **LoginTests** | Login page loads; valid login and redirect; invalid credentials; remember_me; authenticated user redirected away from login. |
| **VerifyEmailViewTests** | Valid token sets is_email_verified and clears token; invalid token redirects with error. |
| **LoginUnverifiedTests** | Unverified user cannot log in (email_not_verified). |
| **ResendVerificationTests** | Rate limiting and resend verification flow. |
| **LoginEdgeCasesTests** | Google-only user hint; inactive user handling; etc. |
| **AccountDeletionTests** | GET/POST request deletion with password; wrong password returns form error; already requested redirects to profile; Google-only user (no password) can request deletion; cancel with valid token restores user; invalid cancel token redirects with error; account_delete_done returns 200. |
| **DeleteExpiredAccountsCommandTests** | Deletes user with deletion_requested_at 31 days ago; does not delete user at 29 days or with no deletion_requested_at; --dry-run does not delete and reports would-delete. |
| **LogoutTests** | POST logs out and redirects to login. |
| **ProfileViewTests** | Anonymous user redirected to login; logged-in user gets 200 with user_info_form and user_profile_form; POST with valid data updates and redirects. |
| **PasswordResetTests** | Form submit and rate limiting. |
| **PasswordResetConfirmTests** | Valid token and set password; invalid token handling. |
| **PasswordChangeTests** | Logged-in user can change password. |
| **CheckEmailAvailabilityTests** | API returns available when email not taken; returns not available when email exists. |
