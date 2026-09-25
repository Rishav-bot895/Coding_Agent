"""Target file resolution, attribute extraction, and permission validation for localdev.

Enforces strict single-target scope by validating explicitly supplied target paths,
extracting immutable metadata facts (path, size, SHA-256, encoding, BOM, newlines),
and auditing security attributes (read-only flag, reparse points / symlinks).

Note on Stale-Edit Detection:
    The baseline SHA-256 hash recorded in TargetRecord guards against stale edits
    (e.g., changes made in an external editor during analysis before atomic replacement).
    It does NOT constitute a concurrent filesystem compare-and-swap (CAS) primitive.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import os
import stat
import tokenize
from pathlib import Path
from typing import Final, Literal

from localdev.constants import MAX_SOURCE_SIZE_BYTES
from localdev.errors import StaleEditError, TargetValidationError
from localdev.schemas import TargetRecord

# Windows File Attribute Constants
WIN_FILE_ATTRIBUTE_READONLY: Final[int] = 0x01
WIN_FILE_ATTRIBUTE_REPARSE_POINT: Final[int] = 0x400


def detect_encoding_and_bom(raw_bytes: bytes) -> tuple[str, bool]:
    """Detect PEP 263 encoding declaration and UTF-8 BOM.

    Args:
        raw_bytes: Raw file content bytes.

    Returns:
        Tuple of (normalized_encoding_name, has_bom_boolean).

    Raises:
        TargetValidationError: If declared encoding is invalid or cannot be decoded.
    """
    has_bom = raw_bytes.startswith(codecs.BOM_UTF8)

    if has_bom:
        encoding = "utf-8"
    elif raw_bytes:
        try:
            detected, _ = tokenize.detect_encoding(io.BytesIO(raw_bytes).readline)
            encoding = detected.lower()
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise TargetValidationError(
                f"Failed to detect source encoding: {exc}",
                details={"error": str(exc)},
            ) from exc
    else:
        encoding = "utf-8"

    # Verify that the entire file can be decoded using the detected encoding
    try:
        raw_bytes.decode(encoding)
    except UnicodeDecodeError as exc:
        raise TargetValidationError(
            f"Target file contains byte sequences invalid for encoding '{encoding}': {exc}",
            details={"encoding": encoding, "error": str(exc)},
        ) from exc

    return encoding, has_bom


def detect_newline_style(raw_bytes: bytes) -> tuple[Literal["\r\n", "\n", "mixed"], bool]:
    """Detect newline style (CRLF, LF, or mixed) and presence of a trailing newline.

    Args:
        raw_bytes: Raw file content bytes.

    Returns:
        Tuple of (newline_style, has_trailing_newline).
    """
    if not raw_bytes:
        return "\n", False

    has_trailing_newline = raw_bytes.endswith((b"\n", b"\r"))

    crlf_count = raw_bytes.count(b"\r\n")
    total_lf = raw_bytes.count(b"\n")
    total_cr = raw_bytes.count(b"\r")

    bare_lf = total_lf - crlf_count
    bare_cr = total_cr - crlf_count

    if crlf_count > 0 and (bare_lf > 0 or bare_cr > 0):
        newline_style: Literal["\r\n", "\n", "mixed"] = "mixed"
    elif crlf_count > 0 and bare_lf == 0 and bare_cr == 0:
        newline_style = "\r\n"
    elif crlf_count == 0 and bare_lf > 0 and bare_cr == 0:
        newline_style = "\n"
    elif bare_cr > 0:
        newline_style = "mixed"
    else:
        # File has no newlines
        newline_style = "\n"

    return newline_style, has_trailing_newline


def get_target_file_attributes(target_path: Path) -> tuple[bool, bool]:
    """Detect reparse-point (symlink/junction) and read-only status on Windows.

    Args:
        target_path: Path to target file.

    Returns:
        Tuple of (is_reparse_point, is_read_only).
    """
    try:
        lstat_result = os.lstat(target_path)
    except OSError:
        return False, False

    # Check for reparse point (symlink or junction)
    is_reparse_point = target_path.is_symlink()
    is_read_only = False

    if hasattr(lstat_result, "st_file_attributes"):
        attrs = lstat_result.st_file_attributes
        if bool(attrs & WIN_FILE_ATTRIBUTE_REPARSE_POINT):
            is_reparse_point = True
        if bool(attrs & WIN_FILE_ATTRIBUTE_READONLY):
            is_read_only = True
    else:
        # Fallback for non-Windows platforms
        is_read_only = not os.access(target_path, os.W_OK)

    return is_reparse_point, is_read_only


def validate_target(
    path: str | Path,
    max_size_bytes: int = MAX_SOURCE_SIZE_BYTES,
    for_write: bool = False,
) -> TargetRecord:
    """Resolve and validate the explicitly supplied single target file.

    Inspects metadata, detects encoding and newline style, computes SHA-256 hash,
    and returns an immutable TargetRecord.

    Args:
        path: Path string or Path object for the target file.
        max_size_bytes: Maximum allowed file size in bytes (default: 256 KB).
        for_write: If True, immediately rejects read-only files and reparse points.

    Returns:
        Immutable TargetRecord.

    Raises:
        TargetValidationError: If target is non-existent, a directory, non-regular,
            oversized, unreadable, or invalid for write authority.
    """
    raw_path_str = str(path).strip()
    if not raw_path_str:
        raise TargetValidationError("Target file path cannot be empty.")

    target_path = Path(raw_path_str)

    # Check existence via lexists to avoid following broken symlinks
    if not os.path.lexists(target_path):
        raise TargetValidationError(
            f"Target file does not exist: '{raw_path_str}'",
            details={"path": raw_path_str},
        )

    # Directories must be rejected immediately
    if target_path.is_dir():
        raise TargetValidationError(
            f"Target path is a directory, not a regular file: '{raw_path_str}'",
            details={"path": raw_path_str},
        )

    # Detect reparse points and read-only status before reading content
    is_reparse_point, is_read_only = get_target_file_attributes(target_path)

    # Read binary bytes
    try:
        raw_bytes = target_path.read_bytes()
    except OSError as exc:
        raise TargetValidationError(
            f"Cannot read target file '{raw_path_str}': {exc}",
            details={"path": raw_path_str, "error": str(exc)},
        ) from exc

    # Enforce regular file check (unless it's a symlink to regular file)
    try:
        lstat_result = os.lstat(target_path)
        if not stat.S_ISREG(lstat_result.st_mode) and not is_reparse_point:
            raise TargetValidationError(
                f"Target is not a regular file: '{raw_path_str}'",
                details={"path": raw_path_str},
            )
    except OSError as exc:
        raise TargetValidationError(
            f"Failed to inspect target file '{raw_path_str}': {exc}",
            details={"path": raw_path_str, "error": str(exc)},
        ) from exc

    # Enforce file size limit
    file_size = len(raw_bytes)
    if file_size > max_size_bytes:
        raise TargetValidationError(
            f"Target file '{raw_path_str}' size ({file_size} bytes) exceeds maximum "
            f"limit of {max_size_bytes} bytes ({max_size_bytes // 1024} KB).",
            details={"path": raw_path_str, "size": file_size, "limit": max_size_bytes},
        )

    # Compute SHA-256 hash for compare-before-replace stale-edit protection
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    # Detect encoding and UTF-8 BOM
    encoding, has_bom = detect_encoding_and_bom(raw_bytes)

    # Detect newline convention and trailing newline
    newline_style, has_trailing_newline = detect_newline_style(raw_bytes)

    # Write target eligibility checks
    if for_write:
        if is_reparse_point:
            raise TargetValidationError(
                f"Target file '{raw_path_str}' is a symlink or reparse point, "
                "which is prohibited as a write target.",
                details={"path": raw_path_str},
            )
        if is_read_only:
            raise TargetValidationError(
                f"Target file '{raw_path_str}' has the Windows read-only attribute set "
                "and cannot be modified.",
                details={"path": raw_path_str},
            )

    try:
        resolved_absolute_path = str(target_path.resolve())
    except OSError:
        resolved_absolute_path = str(target_path.absolute())

    return TargetRecord(
        path=raw_path_str,
        absolute_path=resolved_absolute_path,
        file_size_bytes=file_size,
        sha256=sha256_hash,
        encoding=encoding,
        has_bom=has_bom,
        newline_style=newline_style,
        has_trailing_newline=has_trailing_newline,
        is_read_only=is_read_only,
        is_reparse_point=is_reparse_point,
    )


def assert_writable_target(record: TargetRecord) -> None:
    """Assert that a validated TargetRecord is eligible for write operations.

    Args:
        record: Validated TargetRecord.

    Raises:
        TargetValidationError: If target is read-only or a reparse point.
    """
    if record.is_reparse_point:
        raise TargetValidationError(
            f"Target file '{record.path}' is a symlink or reparse point, "
            "which is prohibited as a write target.",
            details={"path": record.path},
        )
    if record.is_read_only:
        raise TargetValidationError(
            f"Target file '{record.path}' has the Windows read-only attribute set "
            "and cannot be modified.",
            details={"path": record.path},
        )


def verify_target_hash(target_path: str | Path, expected_sha256: str) -> None:
    """Verify that target file on disk matches expected SHA-256 hash.

    Performs compare-before-replace stale-edit verification immediately before
    atomic file replacement to prevent overwriting modifications made outside
    the current localdev session.

    Args:
        target_path: Path to target file.
        expected_sha256: Baseline pre-edit SHA-256 hash.

    Raises:
        TargetValidationError: If target cannot be read or is missing.
        StaleEditError: If current hash does not match expected baseline hash.
    """
    p = Path(target_path)
    if not p.is_file():
        raise TargetValidationError(
            f"Target file does not exist or is not a regular file: '{target_path}'",
            details={"path": str(target_path)},
        )

    try:
        current_bytes = p.read_bytes()
    except OSError as exc:
        raise TargetValidationError(
            f"Failed to read target file '{target_path}' for hash verification: {exc}",
            details={"path": str(target_path), "error": str(exc)},
        ) from exc

    current_hash = hashlib.sha256(current_bytes).hexdigest()
    if current_hash != expected_sha256:
        raise StaleEditError(
            f"Target file '{target_path}' was modified externally (expected SHA-256 "
            f"{expected_sha256[:8]}..., got {current_hash[:8]}...). Atomic replacement aborted.",
            expected_hash=expected_sha256,
            actual_hash=current_hash,
        )


def re_resolve_and_verify_target(
    target_path: str | Path,
    expected_sha256: str,
) -> Path:
    """Re-resolve target path immediately before write and verify all write safety invariants.

    Enforces:
    - Path existence and regular file check.
    - Reparse point (symlink/junction) rejection.
    - Windows read-only attribute rejection.
    - Compare-before-replace SHA-256 stale-edit detection.

    Note on Concurrency Limitation:
        The compare-before-replace SHA-256 check provides stale-edit detection
        against external file modifications occurring between analysis and
        replacement. It is not an OS-level atomic compare-and-swap (CAS) and
        does not eliminate the tiny microsecond race window between hash
        calculation and ReplaceFileW execution.

    Args:
        target_path: Target path to verify.
        expected_sha256: Baseline pre-edit SHA-256 hash.

    Returns:
        Resolved canonical absolute Path on Windows.

    Raises:
        TargetValidationError: If target is missing, a directory, a reparse point, or read-only.
        StaleEditError: If file hash has changed since analysis baseline.
    """
    raw_path_str = str(target_path).strip()
    if not raw_path_str:
        raise TargetValidationError("Target file path cannot be empty.")

    p = Path(raw_path_str)

    if not os.path.lexists(p):
        raise TargetValidationError(
            f"Target file does not exist: '{raw_path_str}'",
            details={"path": raw_path_str},
        )

    if p.is_dir():
        raise TargetValidationError(
            f"Target path is a directory, not a regular file: '{raw_path_str}'",
            details={"path": raw_path_str},
        )

    # Detect reparse points and read-only status before verifying hash
    is_reparse_point, is_read_only = get_target_file_attributes(p)
    if is_reparse_point:
        raise TargetValidationError(
            f"Target file '{raw_path_str}' is a symlink or reparse point, "
            "which is prohibited as a write target.",
            details={"path": raw_path_str},
        )
    if is_read_only:
        raise TargetValidationError(
            f"Target file '{raw_path_str}' has the Windows read-only attribute set "
            "and cannot be modified.",
            details={"path": raw_path_str},
        )

    # Verify baseline SHA-256 hash
    verify_target_hash(p, expected_sha256)

    try:
        return p.resolve()
    except OSError:
        return p.absolute()

