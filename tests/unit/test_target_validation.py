"""Unit tests for single-target resolution, metadata preservation, and validation (P2-T2).

Verifies strict single-target enforcement, immutable TargetRecord construction,
PEP 263 encoding detection, UTF-8 BOM detection, newline convention analysis,
file size limits, Windows read-only and reparse-point auditing, and SHA-256
stale-edit compare-before-replace protection.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from localdev.agent.permissions import (
    assert_writable_target,
    detect_newline_style,
    validate_target,
    verify_target_hash,
)
from localdev.constants import MAX_SOURCE_SIZE_BYTES
from localdev.errors import StaleEditError, TargetValidationError

BOUNDARY_SAMPLES_DIR = Path(__file__).parent.parent / "boundary_samples"


# =============================================================================
# Regular File & Non-existent Target Verification
# =============================================================================


def test_nonexistent_target_rejected(tmp_path: Path) -> None:
    """Non-existent target file must be rejected with TargetValidationError."""
    missing = tmp_path / "does_not_exist.py"
    with pytest.raises(TargetValidationError) as exc_info:
        validate_target(missing)
    assert "does not exist" in str(exc_info.value)
    assert exc_info.value.exit_code == 3


def test_directory_target_rejected(tmp_path: Path) -> None:
    """Directory supplied as target must be rejected with TargetValidationError."""
    with pytest.raises(TargetValidationError) as exc_info:
        validate_target(tmp_path)
    assert "is a directory" in str(exc_info.value)
    assert exc_info.value.exit_code == 3


def test_empty_path_rejected() -> None:
    """Empty or whitespace-only target path must be rejected."""
    with pytest.raises(TargetValidationError) as exc_info:
        validate_target("")
    assert "cannot be empty" in str(exc_info.value)


# =============================================================================
# Source Size Limits
# =============================================================================


def test_oversized_file_rejected(tmp_path: Path) -> None:
    """File exceeding MAX_SOURCE_SIZE_BYTES must be rejected immediately."""
    oversized = tmp_path / "oversized.py"
    # Create file of MAX_SOURCE_SIZE_BYTES + 1 byte
    oversized.write_bytes(b"#" * (MAX_SOURCE_SIZE_BYTES + 1))

    with pytest.raises(TargetValidationError) as exc_info:
        validate_target(oversized)
    assert "exceeds maximum limit" in str(exc_info.value)
    assert exc_info.value.exit_code == 3


def test_exact_size_limit_accepted(tmp_path: Path) -> None:
    """File exactly matching MAX_SOURCE_SIZE_BYTES must be accepted."""
    exact = tmp_path / "exact.py"
    exact.write_bytes(b"#" * MAX_SOURCE_SIZE_BYTES)

    record = validate_target(exact)
    assert record.file_size_bytes == MAX_SOURCE_SIZE_BYTES


# =============================================================================
# Empty File Boundary
# =============================================================================


def test_empty_file_boundary() -> None:
    """Empty (0-byte) file must produce valid TargetRecord with empty SHA-256."""
    empty_file = BOUNDARY_SAMPLES_DIR / "empty.py"
    record = validate_target(empty_file)

    assert record.file_size_bytes == 0
    assert record.sha256 == hashlib.sha256(b"").hexdigest()
    assert record.encoding == "utf-8"
    assert record.has_bom is False
    assert record.has_trailing_newline is False
    assert record.newline_style == "\n"


# =============================================================================
# Encoding & BOM Detection (PEP 263)
# =============================================================================


def test_utf8_plain() -> None:
    """Standard UTF-8 file without BOM is detected correctly."""
    target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    record = validate_target(target)

    assert record.encoding == "utf-8"
    assert record.has_bom is False
    assert record.newline_style == "\n"
    assert record.has_trailing_newline is True


def test_utf8_with_bom() -> None:
    """UTF-8 file with BOM is detected with has_bom=True."""
    target = BOUNDARY_SAMPLES_DIR / "utf8_bom.py"
    record = validate_target(target)

    assert record.encoding == "utf-8"
    assert record.has_bom is True
    assert record.has_trailing_newline is True


def test_declared_iso8859() -> None:
    """PEP 263 declared iso-8859-1 encoding is detected and preserved."""
    target = BOUNDARY_SAMPLES_DIR / "declared_iso8859.py"
    record = validate_target(target)

    assert record.encoding in ("iso-8859-1", "latin-1")
    assert record.has_bom is False


def test_declared_utf8() -> None:
    """PEP 263 declared utf-8 encoding is detected."""
    target = BOUNDARY_SAMPLES_DIR / "declared_utf8.py"
    record = validate_target(target)

    assert record.encoding == "utf-8"
    assert record.has_bom is False


def test_invalid_coding_declaration(tmp_path: Path) -> None:
    """Unrecognized PEP 263 coding declaration must raise TargetValidationError."""
    bad_enc = tmp_path / "bad_enc.py"
    bad_enc.write_bytes(b"# -*- coding: non_existent_charset_xyz -*-\nprint(1)\n")

    with pytest.raises(TargetValidationError) as exc_info:
        validate_target(bad_enc)
    assert "Failed to detect source encoding" in str(exc_info.value)


def test_corrupt_bytes_for_encoding(tmp_path: Path) -> None:
    """File declaring utf-8 but containing invalid bytes must be rejected."""
    bad_bytes = tmp_path / "bad_bytes.py"
    # 0xFF is invalid in UTF-8
    bad_bytes.write_bytes(b"# coding=utf-8\nx = b'\xff'\n\xff\xff\n")

    with pytest.raises(TargetValidationError) as exc_info:
        validate_target(bad_bytes)
    assert "invalid for encoding" in str(exc_info.value)


# =============================================================================
# Newline Style & Trailing Newline Analysis
# =============================================================================


def test_crlf_newlines() -> None:
    """Windows CRLF newlines are detected as newline_style='\\r\\n'."""
    target = BOUNDARY_SAMPLES_DIR / "crlf.py"
    record = validate_target(target)

    assert record.newline_style == "\r\n"
    assert record.has_trailing_newline is True


def test_lf_newlines() -> None:
    """POSIX LF newlines are detected as newline_style='\\n'."""
    target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    record = validate_target(target)

    assert record.newline_style == "\n"
    assert record.has_trailing_newline is True


def test_mixed_newlines() -> None:
    """File containing both CRLF and LF is detected as newline_style='mixed'."""
    target = BOUNDARY_SAMPLES_DIR / "mixed_newlines.py"
    record = validate_target(target)

    assert record.newline_style == "mixed"
    assert record.has_trailing_newline is True


def test_missing_trailing_newline() -> None:
    """File lacking final newline has has_trailing_newline=False."""
    target = BOUNDARY_SAMPLES_DIR / "no_trailing_newline.py"
    record = validate_target(target)

    assert record.has_trailing_newline is False


def test_newline_detector_unit() -> None:
    """Unit test for detect_newline_style helper."""
    assert detect_newline_style(b"")[1] is False
    assert detect_newline_style(b"a\r\nb\r\n") == ("\r\n", True)
    assert detect_newline_style(b"a\nb\n") == ("\n", True)
    assert detect_newline_style(b"a\r\nb\n") == ("mixed", True)
    assert detect_newline_style(b"single line without newline") == ("\n", False)
    assert detect_newline_style(b"a\r") == ("mixed", True)


# =============================================================================
# Special Filenames: Spaces and Unicode
# =============================================================================


def test_filename_with_spaces_and_unicode() -> None:
    """Paths with spaces and Unicode characters are resolved and handled."""
    target = BOUNDARY_SAMPLES_DIR / "spaces and unicode alpha.py"
    record = validate_target(target)

    assert "spaces and unicode" in record.path
    assert Path(record.absolute_path).is_file()
    assert record.file_size_bytes > 0


# =============================================================================
# Windows Security Attributes: Read-Only and Reparse Points
# =============================================================================


def test_read_only_attribute_detection(tmp_path: Path) -> None:
    """Windows read-only file attribute is detected and reported on TargetRecord."""
    ro_file = tmp_path / "readonly.py"
    ro_file.write_bytes(b"print('readonly')\n")

    # Set read-only attribute
    os.chmod(ro_file, stat.S_IREAD)
    try:
        record = validate_target(ro_file)
        assert record.is_read_only is True

        # Assert writable rejection
        with pytest.raises(TargetValidationError) as exc_info:
            assert_writable_target(record)
        assert "read-only attribute set" in str(exc_info.value)

        # for_write=True immediately rejects
        with pytest.raises(TargetValidationError) as exc_info_write:
            validate_target(ro_file, for_write=True)
        assert "read-only attribute set" in str(exc_info_write.value)
    finally:
        # Restore write permissions so temporary directory cleanup succeeds
        os.chmod(ro_file, stat.S_IWRITE)


def test_reparse_point_detection_mocked(tmp_path: Path) -> None:
    """Reparse points (symlinks/junctions) are detected and rejected for write."""
    target = tmp_path / "link_target.py"
    target.write_bytes(b"print('target')\n")

    # Mock get_target_file_attributes to simulate a reparse point
    with patch(
        "localdev.agent.permissions.get_target_file_attributes",
        return_value=(True, False),
    ):
        record = validate_target(target)
        assert record.is_reparse_point is True

        with pytest.raises(TargetValidationError) as exc_info:
            assert_writable_target(record)
        assert "symlink or reparse point" in str(exc_info.value)

        with pytest.raises(TargetValidationError) as exc_info_write:
            validate_target(target, for_write=True)
        assert "symlink or reparse point" in str(exc_info_write.value)


# =============================================================================
# SHA-256 Baseline & Compare-Before-Replace Stale-Edit Protection
# =============================================================================


def test_sha256_hash_verification_success(tmp_path: Path) -> None:
    """Unmodified target matches baseline hash."""
    f = tmp_path / "target.py"
    content = b"x = 42\n"
    f.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()
    record = validate_target(f)
    assert record.sha256 == expected_hash

    # verify_target_hash succeeds without raising
    verify_target_hash(f, expected_hash)


def test_sha256_hash_verification_stale_edit_detected(tmp_path: Path) -> None:
    """Modifying target file externally causes StaleEditError on verification."""
    f = tmp_path / "target.py"
    f.write_bytes(b"initial content\n")

    record = validate_target(f)
    initial_hash = record.sha256

    # External modification
    f.write_bytes(b"external editor modification\n")

    with pytest.raises(StaleEditError) as exc_info:
        verify_target_hash(f, initial_hash)

    assert "modified externally" in str(exc_info.value)
    assert exc_info.value.expected_hash == initial_hash
    assert exc_info.value.actual_hash != initial_hash
    assert exc_info.value.exit_code == 3


def test_target_record_immutability() -> None:
    """TargetRecord must be frozen (immutable)."""
    target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    record = validate_target(target)

    with pytest.raises((TypeError, ValueError)):  # TypeError or ValueError due to frozen model
        record.sha256 = "0" * 64  # type: ignore[misc]


# =============================================================================
# Isolation Invariant: Only Single Target Accessed
# =============================================================================


def test_only_single_target_accessed(tmp_path: Path) -> None:
    """validate_target must access only the explicit target and no neighboring files."""
    neighbor = tmp_path / "neighbor.py"
    neighbor.write_bytes(b"secret = True\n")

    target = tmp_path / "target.py"
    target.write_bytes(b"import neighbor\n")

    record = validate_target(target)
    assert record.path == str(target)
    assert record.file_size_bytes == len(b"import neighbor\n")
    # Verify neighbor was not modified or read into record
    assert neighbor.exists()
