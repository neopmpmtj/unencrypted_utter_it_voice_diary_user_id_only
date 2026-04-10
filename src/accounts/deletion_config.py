"""
Admin-configurable parameters for account deletion flow.

Uses GlobalSettings for runtime configuration. Keys:
- accounts.deletion_grace_days: Days user can cancel via email link (default 30)
- accounts.deletion_retention_days: Days before permanent deletion (default 90)
"""

from src.accounts.models import GlobalSettings


def get_deletion_grace_days() -> int:
    """Days user can cancel account deletion via email link."""
    value = GlobalSettings.get_value('accounts.deletion_grace_days', 30)
    return int(value) if value is not None else 30


def get_deletion_retention_days() -> int:
    """Days before user data is permanently deleted from the database."""
    value = GlobalSettings.get_value('accounts.deletion_retention_days', 90)
    return int(value) if value is not None else 90
