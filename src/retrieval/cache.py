"""
Prep-cache for the two-stage indexing pipeline.

Stage 1 (prep task) decrypts data and writes it here so Stage 2
(process task) can read plaintext without re-decrypting.

Supports a file backend; Redis can be added later via INDEX_CACHE_BACKEND.
"""

import json
import logging
import os
import secrets

from decouple import config
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

INDEX_CACHE_DIR = config("INDEX_CACHE_DIR", default="/tmp/utter_it_index_cache")
INDEX_CACHE_TTL = int(config("INDEX_CACHE_TTL", default="900"))


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def write_prep_cache(entry_id: str, user_id: str, data: Dict[str, Any]) -> str:
    """Write prep payload to a temp file. Returns the cache path."""
    base = Path(INDEX_CACHE_DIR) / str(user_id)
    _ensure_dir(base)
    nonce = secrets.token_hex(4)
    filename = f"{entry_id}.{nonce}.tmp"
    path = base / filename
    payload = json.dumps(data, ensure_ascii=False)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    logger.debug("Wrote prep cache %s (%d bytes)", path, len(payload))
    return str(path)


def read_prep_cache(cache_path: str) -> Optional[Dict[str, Any]]:
    """Read and return cached prep data, or None if expired / missing."""
    path = Path(cache_path)
    if not path.exists():
        logger.debug("Cache miss (file not found): %s", cache_path)
        return None
    age = time.time() - path.stat().st_mtime
    if age > INDEX_CACHE_TTL:
        logger.debug("Cache miss (expired, age=%.0fs): %s", age, cache_path)
        _safe_delete(path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cache read error for %s: %s", cache_path, exc)
        return None


def delete_prep_cache(cache_path: str) -> None:
    """Remove cache file after successful processing."""
    _safe_delete(Path(cache_path))


def _safe_delete(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete cache file %s: %s", path, exc)
