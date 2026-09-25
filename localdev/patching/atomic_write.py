"""Atomic target replacement with native Win32 ReplaceFileW and same-volume staging.

Enforces:
- Explicit write authority: interactive confirmation or noninteractive --apply.
  Note: --apply grants write authority in CI/noninteractive mode, but CANNOT bypass
  any validation, hash check, or safety invariant. There is no --force bypass.
- Target re-resolution: re-resolves target path immediately before write and rejects
  reparse points (symlinks/NTFS junctions) and read-only files.
- Compare-before-replace SHA-256 stale-edit detection: detects external file modifications
  occurring between analysis start and replacement.
  Note on Concurrency Limitation: The SHA-256 compare-before-replace check provides
  stale-edit detection against external file modifications, not a true concurrent
  filesystem compare-and-swap (CAS); it does not eliminate the tiny microsecond race
  window between hash calculation and ReplaceFileW.
- Same-volume staging architecture: Win32 ReplaceFileW strictly requires target,
  replacement candidate, and backup to reside on the same filesystem volume.
  Cross-volume attempts fail closed with an explicit error.
- Native atomic backup via ReplaceFileW: uses the native lpBackupFileName parameter
  (flags=0; unsupported REPLACEFILE_WRITE_THROUGH omitted per Win32 API docs).
  If replacement fails, original file is preserved and no backup is created or corrupted.
  If replacement succeeds, backup is guaranteed to reflect the pre-patch state.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.errors import PatchApplicationError
from localdev.patching.backup import get_backup_path

if TYPE_CHECKING:
    from localdev.patching.applier import PatchCandidate

if os.name == "nt":
    from ctypes import wintypes


def get_volume_for_path(path: Path | str) -> str:
    """Determine the filesystem volume identifier for a path on Windows.

    Uses Win32 GetVolumePathNameW when available on Windows (NT), falling back
    to Path.drive or os.path.splitdrive.

    Args:
        path: Path string or Path object.

    Returns:
        Normalized volume root (e.g. 'D:' or 'C:').
    """
    resolved = Path(path).resolve()
    if os.name == "nt":
        with contextlib.suppress(OSError, ValueError, AttributeError):
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.GetVolumePathNameW(str(resolved), buf, 1024):
                val = buf.value.rstrip("\\").upper()
                if val:
                    return val

    drive = resolved.drive.upper()
    if drive:
        return drive
    anchor = resolved.anchor.upper().rstrip("\\")
    return anchor or "/"


def assert_same_volume(*paths: Path | str) -> None:
    """Ensure all provided paths reside on the exact same filesystem volume.

    Win32 ReplaceFileW strictly requires lpReplacedFileName, lpReplacementFileName,
    and lpBackupFileName to reside on the same volume.

    Args:
        *paths: File or directory paths to check.

    Raises:
        PatchApplicationError: If any two paths reside on different volumes.
    """
    if len(paths) <= 1:
        return

    first = Path(paths[0])
    first_vol = get_volume_for_path(first)

    for other in paths[1:]:
        other_path = Path(other)
        other_vol = get_volume_for_path(other_path)
        if first_vol != other_vol:
            raise PatchApplicationError(
                f"Cross-volume replacement prohibited: '{first}' resides on volume '{first_vol}', "
                f"while '{other_path}' resides on volume '{other_vol}'. "
                "Win32 ReplaceFileW strictly requires target, candidate, and backup to "
                "reside on the same filesystem volume."
            )


def get_same_volume_staging_dir(
    target_path: Path | str,
    session_id: str | None = None,
) -> Path:
    """Determine a secure candidate staging directory on the target's filesystem volume.

    Guarantees that candidate files reside on the same volume as the target file
    for Win32 ReplaceFileW compatibility.

    Args:
        target_path: Path to target file.
        session_id: Optional session identifier for subfolder isolation.

    Returns:
        Resolved absolute Path to staging directory (created if needed).
    """
    target = Path(target_path).resolve()
    subfolder = session_id if session_id else "atomic_replace"
    staging_dir = target.parent / ".localdev_staging" / subfolder
    staging_dir.mkdir(parents=True, exist_ok=True)
    return staging_dir.resolve()


def prompt_confirmation(prompt: str = "Apply this patch? [y/N] ") -> bool:
    """Prompt user interactively for write confirmation.

    Args:
        prompt: Confirmation prompt text.

    Returns:
        True if user confirmed with 'y' or 'yes' (case-insensitive), False otherwise.
    """
    try:
        resp = input(prompt).strip().lower()
        return resp in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def win32_replace_file(
    target_path: Path | str,
    replacement_path: Path | str,
    backup_path: Path | str | None = None,
) -> None:
    """Perform atomic file replacement using native Windows Win32 ReplaceFileW.

    Flags are explicitly passed as 0 per Microsoft Win32 documentation
    (REPLACEFILE_WRITE_THROUGH is not supported for ReplaceFileW).

    Args:
        target_path: Target file being replaced (lpReplacedFileName).
        replacement_path: Replacement candidate file (lpReplacementFileName).
        backup_path: Optional backup path (lpBackupFileName). None if no backup requested.

    Raises:
        PatchApplicationError: If ReplaceFileW fails.
    """
    target = Path(target_path).resolve()
    replacement = Path(replacement_path).resolve()
    backup = Path(backup_path).resolve() if backup_path is not None else None

    # Enforce same volume
    if backup is not None:
        assert_same_volume(target, replacement, backup)
    else:
        assert_same_volume(target, replacement)

    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReplaceFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        kernel32.ReplaceFileW.restype = wintypes.BOOL

        lp_replaced = str(target)
        lp_replacement = str(replacement)
        lp_backup = str(backup) if backup is not None else None

        res = kernel32.ReplaceFileW(
            lp_replaced,
            lp_replacement,
            lp_backup,
            0,  # dwReplaceFlags = 0
            None,  # lpExclude = NULL
            None,  # lpReserved = NULL
        )

        if not res:
            err = ctypes.get_last_error()
            raise PatchApplicationError(
                f"Win32 ReplaceFileW failed on target '{target}' with error {err}: "
                f"{ctypes.FormatError(err).strip()}"
            )
    else:
        # Fallback for non-Windows test environments
        try:
            if backup is not None:
                shutil.copy2(target, backup)
            os.replace(replacement, target)
        except OSError as exc:
            raise PatchApplicationError(f"Atomic replacement fallback failed: {exc}") from exc


class AtomicReplacementResult:
    """Outcome of an atomic replacement operation."""

    def __init__(
        self,
        success: bool,
        target_path: Path,
        applied_sha256: str | None = None,
        backup_created: bool = False,
        backup_path: Path | None = None,
        staged_path: Path | None = None,
        message: str = "",
    ) -> None:
        self.success = success
        self.target_path = target_path
        self.applied_sha256 = applied_sha256
        self.backup_created = backup_created
        self.backup_path = backup_path
        self.staged_path = staged_path
        self.message = message

    def __repr__(self) -> str:
        return (
            f"AtomicReplacementResult(success={self.success}, target='{self.target_path.name}', "
            f"backup={self.backup_created}, message='{self.message}')"
        )


def atomic_replace_file(
    target_path: Path | str,
    candidate: PatchCandidate | Path | str,
    baseline_sha256: str,
    has_write_authority: bool = False,
    interactive: bool = True,
    create_backup: bool = True,
    backup_path: Path | str | None = None,
    staging_dir: Path | str | None = None,
    prompt_func: Callable[[str], bool] | None = None,
) -> AtomicReplacementResult:
    """Atomically replace target file after validating write authority and safety gates.

    Pipeline:
    1. Write authority check: grants write authority if has_write_authority=True (--apply)
       or interactive confirmation prompt succeeds.
       Note: has_write_authority (--apply) never bypasses any validation or safety checks.
    2. Target re-resolution & stale-edit detection: re-resolves target path immediately
       before write, rejecting reparse points (symlinks/junctions), read-only files,
       and external modifications (mismatched baseline SHA-256).
    3. Same-volume candidate staging: ensures candidate is staged on the target's volume.
    4. Native Win32 ReplaceFileW execution with optional lpBackupFileName backup.

    Args:
        target_path: Target file path to update.
        candidate: PatchCandidate instance or Path to candidate replacement file.
        baseline_sha256: Baseline pre-edit SHA-256 hash recorded before patch proposal.
        has_write_authority: True if --apply was passed (noninteractive write permission).
        interactive: True if interactive user prompt is permitted.
        create_backup: True to create native atomic backup (<target>.bak).
        backup_path: Optional explicit backup path (must reside on same volume).
        staging_dir: Optional explicit staging directory on the target's volume.
        prompt_func: Optional custom interactive prompt function for testing.

    Returns:
        AtomicReplacementResult with operation details.

    Raises:
        PatchApplicationError: If write authority is missing, cross-volume staging occurs,
            or ReplaceFileW fails.
        TargetValidationError: If target is missing, a directory, a reparse point, or read-only.
        StaleEditError: If target file was modified externally since analysis baseline.
    """
    # Step 1: Write Authority Check
    if not has_write_authority:
        if interactive:
            ask = prompt_func if prompt_func is not None else prompt_confirmation
            confirmed = ask("Apply this patch? [y/N] ")
            if not confirmed:
                return AtomicReplacementResult(
                    success=False,
                    target_path=Path(target_path),
                    message="Patch application aborted by user.",
                )
        else:
            raise PatchApplicationError(
                "Write authority required to replace target file. "
                "Run interactively or provide the --apply flag."
            )

    # Step 2: Target Re-Resolution & Safety Verification (Reparse Point, Read-Only, Stale Hash)
    from localdev.agent.permissions import re_resolve_and_verify_target

    resolved_target = re_resolve_and_verify_target(target_path, baseline_sha256)

    # Step 3: Candidate Staging on Same Volume
    # Check if candidate is a PatchCandidate instance
    staged_candidate_path: Path
    if hasattr(candidate, "write_to_staging"):
        target_vol_staging = (
            Path(staging_dir).resolve()
            if staging_dir is not None
            else get_same_volume_staging_dir(resolved_target)
        )
        assert_same_volume(resolved_target, target_vol_staging)
        staged_candidate_path = candidate.write_to_staging(target_vol_staging)
    else:
        staged_candidate_path = Path(candidate).resolve()
        if not staged_candidate_path.is_file():
            raise PatchApplicationError(
                f"Replacement candidate file not found: {staged_candidate_path}"
            )
        assert_same_volume(resolved_target, staged_candidate_path)

    # Step 4: Backup Path Determination
    resolved_backup_path: Path | None = None
    if create_backup:
        resolved_backup_path = (
            Path(backup_path).resolve()
            if backup_path is not None
            else get_backup_path(resolved_target)
        )
        assert_same_volume(resolved_target, resolved_backup_path)

    # Step 5: Native Win32 Atomic Replacement
    win32_replace_file(
        target_path=resolved_target,
        replacement_path=staged_candidate_path,
        backup_path=resolved_backup_path,
    )

    # Step 6: Post-Replacement Hash Calculation
    new_sha256 = hashlib.sha256(resolved_target.read_bytes()).hexdigest()

    # Step 7: Clean up staging directory if empty
    try:
        staging_parent = staged_candidate_path.parent
        if (
            (staging_parent.name.startswith(".localdev_staging") or ".localdev_staging" in str(staging_parent))
            and not any(staging_parent.iterdir())
        ):
            staging_parent.rmdir()
            if (
                staging_parent.parent.name == ".localdev_staging"
                and not any(staging_parent.parent.iterdir())
            ):
                staging_parent.parent.rmdir()
    except OSError:
        pass

    return AtomicReplacementResult(
        success=True,
        target_path=resolved_target,
        applied_sha256=new_sha256,
        backup_created=create_backup,
        backup_path=resolved_backup_path,
        staged_path=staged_candidate_path,
        message=f"Atomically replaced '{resolved_target.name}' (SHA-256: {new_sha256[:8]}...).",
    )
