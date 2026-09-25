"""Target file backup management for atomic replacement on Windows.

Provides:
- get_backup_path: Determines same-volume native backup destination (<target>.bak).
- verify_backup: Validates backup file existence, regularity, and SHA-256 integrity.
- restore_backup: Restores target file from a native backup copy.
- remove_backup: Safely cleans up backup files when requested.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Final

from localdev.errors import PatchApplicationError

DEFAULT_BACKUP_SUFFIX: Final[str] = ".bak"


def get_backup_path(target_path: Path | str, suffix: str = DEFAULT_BACKUP_SUFFIX) -> Path:
    """Determine the default same-volume native backup path for a target file.

    Guaranteed to reside in the exact same directory (and therefore on the same
    filesystem volume) as the target file.

    Args:
        target_path: Path to target file.
        suffix: Suffix appended to target path (default: '.bak').

    Returns:
        Resolved absolute Path for the backup file.
    """
    p = Path(target_path).resolve()
    return p.with_name(f"{p.name}{suffix}")


def verify_backup(
    backup_path: Path | str,
    expected_sha256: str | None = None,
) -> bool:
    """Verify that a backup file exists, is a regular file, and matches expected content hash.

    Args:
        backup_path: Path to backup file.
        expected_sha256: Optional expected SHA-256 hash to verify backup integrity.

    Returns:
        True if backup exists and passes validation.

    Raises:
        PatchApplicationError: If backup does not exist or SHA-256 mismatches.
    """
    p = Path(backup_path).resolve()
    if not p.is_file():
        raise PatchApplicationError(f"Backup file does not exist or is not a regular file: {p}")

    if expected_sha256 is not None:
        try:
            actual_sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError as exc:
            raise PatchApplicationError(f"Failed to read backup file '{p}': {exc}") from exc

        if actual_sha256 != expected_sha256:
            raise PatchApplicationError(
                f"Backup file integrity mismatch for '{p}': expected SHA-256 "
                f"{expected_sha256[:8]}..., got {actual_sha256[:8]}..."
            )

    return True


def restore_backup(target_path: Path | str, backup_path: Path | str) -> None:
    """Restore target file from a backup copy.

    Args:
        target_path: Destination target file path to restore.
        backup_path: Source backup file path.

    Raises:
        PatchApplicationError: If restoration fails.
    """
    t = Path(target_path).resolve()
    b = Path(backup_path).resolve()

    if not b.is_file():
        raise PatchApplicationError(f"Cannot restore from non-existent backup: {b}")

    try:
        shutil.copy2(b, t)
    except OSError as exc:
        raise PatchApplicationError(f"Failed to restore target from backup '{b}': {exc}") from exc


def remove_backup(backup_path: Path | str) -> bool:
    """Safely remove a backup file if it exists.

    Args:
        backup_path: Path to backup file.

    Returns:
        True if removed, False if file did not exist.

    Raises:
        PatchApplicationError: If removal fails due to OS permissions/locking.
    """
    p = Path(backup_path)
    try:
        if p.is_file() or os.path.lexists(p):
            p.unlink()
            return True
        return False
    except OSError as exc:
        raise PatchApplicationError(f"Failed to remove backup file '{p}': {exc}") from exc
