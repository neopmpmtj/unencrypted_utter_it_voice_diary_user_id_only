"""
Encryption Key Configuration Module

This module manages the master encryption key independently from Django settings.
The encryption key is kept separate from SECRET_KEY to allow independent key rotation
and to maintain clear separation of concerns.

Key Features:
- Loads MASTER_ENCRYPTION_KEY directly from environment (not via Django settings)
- Validates Fernet key format on import
- Raises error if key is missing or invalid
- Provides single source of truth for encryption key management
- Master key rotation supported via: python manage.py rotate_master_encryption_key
  (see docs/MASTER_KEY_ROTATION_RUNBOOK.md)
"""

from decouple import config, UndefinedValueError
from cryptography.fernet import Fernet

# Load the master encryption key from environment
# This is intentionally separate from Django settings for security and flexibility
try:
    MASTER_ENCRYPTION_KEY = config('MASTER_ENCRYPTION_KEY')
except UndefinedValueError:
    raise ValueError(
        "MASTER_ENCRYPTION_KEY not found in environment variables. "
        "Please set MASTER_ENCRYPTION_KEY in your .env file or environment. "
        "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )

# Validate that the key is in valid Fernet format
try:
    # This will raise an exception if the key is invalid
    Fernet(MASTER_ENCRYPTION_KEY.encode('utf-8') if isinstance(MASTER_ENCRYPTION_KEY, str) else MASTER_ENCRYPTION_KEY)
except Exception as e:
    raise ValueError(
        f"MASTER_ENCRYPTION_KEY is not a valid Fernet key: {e}. "
        "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )
