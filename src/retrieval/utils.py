"""
Shared utilities for retrieval — HMAC token logic used by indexing and query.
"""

import hashlib
import hmac

from decouple import config

HMAC_KEY = config("INDEX_HMAC_KEY", default=config("SECRET_KEY", default="changeme"))


def hmac_token(token: str) -> str:
    """Compute HMAC-SHA256 of a lowercased token for blind keyword search."""
    return hmac.new(
        HMAC_KEY.encode("utf-8"),
        token.lower().strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
