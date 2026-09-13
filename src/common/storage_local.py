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


def allocate_unique_attachment_filename(
    directory: Path, safe_name: str, used: set[str]
) -> str:
    """
    Pick a filename under ``directory`` that is not in ``used`` and does not
    clobber an existing file. Names already written in the same batch must be
    listed in ``used``; this function adds the returned name to ``used``.

    Collisions get numeric suffixes before the extension: ``a.pdf``, ``a_1.pdf``, …
    """
    base = (safe_name or "").strip() or "uploaded_file"
    path_from = Path(base)
    stem = path_from.stem or "file"
    suffix = path_from.suffix
    candidate = base
    n = 1
    while candidate in used or (directory / candidate).exists():
        if suffix:
            candidate = f"{stem}_{n}{suffix}"
        else:
            candidate = f"{stem}_{n}"
        n += 1
        if n > 10_000:
            raise OSError(f"Could not allocate unique filename for {safe_name!r}")
    used.add(candidate)
    return candidate


def resolve_local_storage_root(config: AppConfig) -> Path:
    """
    Absolute path of the local storage root.

    Refuses anything that is not a real, non-empty, absolute path.

    This guards a silent-junk failure mode: an unset or mocked root used to be
    accepted (``Path(<Mock>)`` resolves against the process CWD), so the storage
    writer created a stray directory tree in the working directory — the
    "MagicMock/" artifact that kept reappearing during test runs. A
    misconfiguration must fail loudly, not litter the filesystem.

    Production is unaffected: ``local_storage_root`` is configured as an absolute
    path. Callers already handle per-file failures, so an unusable root degrades
    to "nothing stored" instead of writing somewhere unexpected.
    """
    raw = config.storage.local_storage_root
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            "storage.local_storage_root is not configured; expected an absolute path "
            f"(got {raw!r})"
        )
    root = Path(raw.strip()).expanduser()
    if not root.is_absolute():
        raise ValueError(
            f"storage.local_storage_root must be an absolute path (got {raw!r})"
        )
    return root.resolve()


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

    Read-only: does not create directories. Misconfiguration (e.g. a file where
    the temp audio path should be) yields False instead of raising.
    """
    try:
        fp = Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False

    temp_base = Path(config.storage.audio_temp_path).expanduser()
    if not str(temp_base).strip():
        return False
    if temp_base.exists() and not temp_base.is_dir():
        return False
    try:
        temp_root = temp_base.resolve(strict=False)
    except (OSError, RuntimeError):
        return False

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
    )
    if user_rec.exists() and not user_rec.is_dir():
        return False
    try:
        user_rec_resolved = user_rec.resolve(strict=False)
    except (OSError, RuntimeError):
        return False

    try:
        fp.relative_to(user_rec_resolved)
        return True
    except ValueError:
        return False


def is_attachment_path_allowed_for_user(
    config: AppConfig, file_path: Path, user_id: int
) -> bool:
    """
    True if file_path resolves under this user's local attachments directory:
    ``{local_storage_root}/{local_attachments_subdir}/{user_id}/``.

    Read-only: does not create directories. When local filesystem storage is
    disabled or misconfigured, returns False.
    """
    if not config.storage.save_attachments_to_local_filesystem:
        return False
    root_raw = (config.storage.local_storage_root or "").strip()
    if not root_raw:
        return False
    try:
        fp = Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False

    try:
        storage_root = resolve_local_storage_root(config)
    except (OSError, RuntimeError):
        return False

    user_att = storage_root / config.storage.local_attachments_subdir / str(user_id)
    if user_att.exists() and not user_att.is_dir():
        return False
    try:
        user_att_resolved = user_att.resolve(strict=False)
    except (OSError, RuntimeError):
        return False

    try:
        fp.relative_to(user_att_resolved)
        return True
    except ValueError:
        return False
