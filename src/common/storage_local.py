"""
Persist attachments and recordings on the host filesystem when Drive uploads are disabled.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.common.config.settings import AppConfig


def sanitize_storage_filename(name: str) -> str:
    """Safe filename for local storage (same rules as Drive upload helper)."""
    if not name or not name.strip():
        return "uploaded_file"
    safe = re.sub(r"[^\w.\- ]", "", name.strip())
    if not safe:
        return "uploaded_file"
    if len(safe) <= 200:
        return safe
    dot_pos = safe.rfind(".")
    if dot_pos > 0:
        ext = safe[dot_pos:]
        stem = safe[:dot_pos]
        max_stem = 200 - len(ext)
        return stem[:max_stem] + ext
    return safe[:200]


def resolve_local_storage_root(config: AppConfig) -> Path:
    root = (config.storage.local_storage_root or "").strip()
    return Path(root).expanduser().resolve()


def ensure_local_storage_tree(config: AppConfig) -> None:
    """Create attachments and recordings roots idempotently; fail if a path exists but is not a directory."""
    if not config.storage.save_attachments_to_local_filesystem:
        return
    root = resolve_local_storage_root(config)
    att = root / config.storage.local_attachments_subdir
    rec = root / config.storage.local_recordings_subdir
    for d in (att, rec):
        if d.exists() and not d.is_dir():
            raise NotADirectoryError(
                f"Local storage path exists but is not a directory: {d}"
            )
        d.mkdir(parents=True, exist_ok=True)


def local_attachments_dir_for_item(config: AppConfig, user_id: int, item_id) -> Path:
    ensure_local_storage_tree(config)
    root = resolve_local_storage_root(config)
    d = root / config.storage.local_attachments_subdir / str(user_id) / str(item_id)
    if d.exists() and not d.is_dir():
        raise NotADirectoryError(
            f"Local storage path exists but is not a directory: {d}"
        )
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_recording_user_dir(config: AppConfig, user_id: int) -> Path:
    ensure_local_storage_tree(config)
    root = resolve_local_storage_root(config)
    d = root / config.storage.local_recordings_subdir / str(user_id)
    if d.exists() and not d.is_dir():
        raise NotADirectoryError(
            f"Local storage path exists but is not a directory: {d}"
        )
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_audio_storage_path_allowed_for_user(
    config: AppConfig, file_path: Path, user_id: int
) -> bool:
    """
    True if file_path is under the temp audio tree or, when local mode is on,
    under this user's permanent recordings directory.
    """
    from src.common.utils.file_sys_utils import ensure_directory

    fp = file_path.resolve()
    temp_root = ensure_directory(config.storage.audio_temp_path).resolve()
    try:
        fp.relative_to(temp_root)
        return True
    except ValueError:
        pass
    if not config.storage.save_attachments_to_local_filesystem:
        return False
    user_rec = (
        resolve_local_storage_root(config)
        / config.storage.local_recordings_subdir
        / str(user_id)
    ).resolve()
    try:
        fp.relative_to(user_rec)
        return True
    except ValueError:
        return False
