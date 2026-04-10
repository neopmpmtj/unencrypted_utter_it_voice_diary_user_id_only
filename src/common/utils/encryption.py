"""
OAuth token encryption (Fernet with MASTER_ENCRYPTION_KEY).

Application data (diary, retrieval, lists) is stored plaintext in the database.
Only Google OAuth fields on UserSecret use encrypt_value / decrypt_value.
"""

from functools import lru_cache
from typing import Optional, Union

from cryptography.fernet import Fernet, InvalidToken

from src.common.encryption_config import MASTER_ENCRYPTION_KEY
from src.common.logging_utils.logging_config import get_logger

logger = get_logger("encryption")


def _key_bytes(master_key: Union[str, bytes]) -> bytes:
    if isinstance(master_key, bytes):
        return master_key
    return master_key.encode("utf-8")


@lru_cache(maxsize=1)
def _default_fernet() -> Fernet:
    return Fernet(_key_bytes(MASTER_ENCRYPTION_KEY))


def _fernet_for_master(master_key: Union[str, bytes]) -> Fernet:
    return Fernet(_key_bytes(master_key))


def encrypt_value(value: str) -> str:
    """Encrypt a string with MASTER_ENCRYPTION_KEY (OAuth tokens, etc.)."""
    if value is None:
        raise ValueError("value cannot be None")
    if not isinstance(value, str):
        value = str(value)
    token = _default_fernet().encrypt(value.encode("utf-8"))
    return token.decode("ascii")


def decrypt_value(encrypted_value: str) -> Optional[str]:
    """Decrypt a value produced by encrypt_value. Returns None on failure."""
    if not encrypted_value:
        logger.warning("Empty encrypted_value provided")
        return None
    try:
        return _default_fernet().decrypt(encrypted_value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as e:
        logger.error(f"Failed to decrypt value: {e}")
        return None


def encrypt_value_with_master(value: str, master_key: Union[str, bytes]) -> str:
    """Encrypt with an explicit Fernet key (master key rotation only)."""
    if value is None:
        raise ValueError("value cannot be None")
    if not isinstance(value, str):
        value = str(value)
    token = _fernet_for_master(master_key).encrypt(value.encode("utf-8"))
    return token.decode("ascii")


def decrypt_value_with_master(
    encrypted_value: str, master_key: Union[str, bytes]
) -> Optional[str]:
    """Decrypt with an explicit Fernet key (master key rotation only)."""
    if not encrypted_value:
        return None
    try:
        return (
            _fernet_for_master(master_key)
            .decrypt(encrypted_value.encode("ascii"))
            .decode("utf-8")
        )
    except (InvalidToken, ValueError, UnicodeError) as e:
        logger.error(f"Failed to decrypt with explicit master key: {e}")
        return None
