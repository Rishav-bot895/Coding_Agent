"""Unit tests for localdev session directory management, manifests, and staging (P2-T3).

Verifies UUID session generation, .localdev_session ownership marker creation,
target copying to session_target.py, manifest serialization, same-volume staging
resolution, fail-closed cross-volume checks, missing marker deletion protection,
and --keep-session retention.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from localdev.agent.permissions import validate_target
from localdev.agent.session import (
    Session,
    SessionManifest,
    generate_session_id,
    get_path_volume,
    is_same_volume,
    resolve_same_volume_staging_dir,
    safely_delete_directory,
    verify_session_ownership_marker,
)
from localdev.constants import (
    SESSION_MARKER_FILENAME,
    SESSION_ROOT_DIRNAME,
    SESSION_TARGET_FILENAME,
)
from localdev.errors import TargetValidationError

BOUNDARY_SAMPLES_DIR = Path(__file__).parent.parent / "boundary_samples"


# =============================================================================
# Session ID & Manifest Creation
# =============================================================================


def test_generate_session_id() -> None:
    """Session IDs must be valid UUIDv4 strings."""
    sid = generate_session_id()
    parsed_uuid = uuid.UUID(sid, version=4)
    assert str(parsed_uuid) == sid


def test_session_initialization_and_manifest(tmp_path: Path) -> None:
    """Initializing session creates dir, marker, session_target.py, and manifest.json."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(
        target_record=target_record,
        base_temp_dir=tmp_path,
    )

    manifest = session.initialize()
    assert isinstance(manifest, SessionManifest)
    assert manifest.session_id == session.session_id

    # Verify session directory created
    assert session.session_dir.is_dir()

    # Verify ownership marker created
    assert session.marker_file.is_file()
    assert verify_session_ownership_marker(session.session_dir) is True
    marker_data = json.loads(session.marker_file.read_text(encoding="utf-8"))
    assert marker_data["session_id"] == session.session_id
    assert marker_data["target"] == target_record.absolute_path

    # Verify target copied to session_target.py
    assert session.session_target_file.is_file()
    assert session.session_target_file.name == SESSION_TARGET_FILENAME
    assert session.session_target_file.read_bytes() == sample_target.read_bytes()

    # Verify manifest.json created
    assert session.manifest_file.is_file()
    manifest_data = json.loads(session.manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["session_id"] == session.session_id
    assert manifest_data["target_sha256"] == target_record.sha256


# =============================================================================
# Same-Volume Staging Architecture
# =============================================================================


def test_volume_resolution_and_comparison() -> None:
    """get_path_volume extracts volume drive letter; is_same_volume compares correctly."""
    assert get_path_volume("C:\\Windows\\System32") == "C:"
    assert get_path_volume("c:/temp/foo.py") == "C:"
    assert get_path_volume("D:\\projects\\bar.py") == "D:"

    assert is_same_volume("C:\\a\\b.py", "C:\\x\\y.py") is True
    assert is_same_volume("C:\\a\\b.py", "D:\\x\\y.py") is False


def test_same_volume_staging_same_drive(tmp_path: Path) -> None:
    """When target and temp reside on same volume, staging is established on that volume."""
    target_file = tmp_path / "target.py"
    target_file.write_bytes(b"x = 1\n")

    sid = generate_session_id()
    staging_dir = resolve_same_volume_staging_dir(
        target_path=target_file,
        session_id=sid,
        base_temp_dir=tmp_path,
    )

    assert is_same_volume(staging_dir, target_file) is True
    assert sid in str(staging_dir)


def test_session_create_same_volume_staging_dir(tmp_path: Path) -> None:
    """Session.create_same_volume_staging_dir establishes staging with ownership marker."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(
        target_record=target_record,
        base_temp_dir=tmp_path,
    )
    session.initialize()

    staging_dir = session.create_same_volume_staging_dir()
    assert staging_dir.is_dir()
    assert is_same_volume(staging_dir, sample_target) is True

    # Marker must exist in staging dir
    staging_marker = staging_dir / SESSION_MARKER_FILENAME
    assert staging_marker.is_file()
    assert verify_session_ownership_marker(staging_dir) is True

    # Cleanup cleans both session dir and staging dir
    session.cleanup()
    assert not session.session_dir.exists()
    assert not staging_dir.exists()


# =============================================================================
# Ownership Marker & Deletion Safety
# =============================================================================


def test_safely_delete_directory_aborts_without_marker(tmp_path: Path) -> None:
    """Attempting to delete a directory without .localdev_session marker aborts fail-closed."""
    foreign_dir = tmp_path / "user_important_project"
    foreign_dir.mkdir()
    important_file = foreign_dir / "data.csv"
    important_file.write_text("critical,data\n", encoding="utf-8")

    # Missing ownership marker must raise TargetValidationError
    with pytest.raises(TargetValidationError) as exc_info:
        safely_delete_directory(foreign_dir)

    assert "missing session ownership marker" in str(exc_info.value)
    # Important directory and file must remain completely untouched
    assert foreign_dir.is_dir()
    assert important_file.is_file()


def test_safely_delete_directory_success_with_marker(tmp_path: Path) -> None:
    """Directory with .localdev_session marker is successfully deleted."""
    session_dir = tmp_path / "session_xyz"
    session_dir.mkdir()
    marker = session_dir / SESSION_MARKER_FILENAME
    marker.write_text("{}", encoding="utf-8")

    subfile = session_dir / "target.py"
    subfile.write_text("print(1)\n", encoding="utf-8")

    safely_delete_directory(session_dir)
    assert not session_dir.exists()


# =============================================================================
# Keep-Session Retention Flag
# =============================================================================


def test_keep_session_retains_directory(tmp_path: Path) -> None:
    """When keep_session=True, session directory and files are preserved after cleanup."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(
        target_record=target_record,
        keep_session=True,
        base_temp_dir=tmp_path,
    )

    with session:
        assert session.session_dir.is_dir()
        assert session.session_target_file.is_file()

    # After context exit, session directory must still exist because keep_session=True
    assert session.session_dir.is_dir()
    assert session.session_target_file.is_file()
    assert session.manifest_file.is_file()


# =============================================================================
# Isolation: Unrelated Files in %TEMP% Untouched
# =============================================================================


def test_unrelated_files_in_temp_untouched(tmp_path: Path) -> None:
    """Cleanup of one session must never touch neighboring sessions or unrelated files."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    # Simulate an unrelated application file in temp
    unrelated_file = tmp_path / "unrelated_temp_data.bin"
    unrelated_file.write_bytes(b"some other process data")

    # Simulate a sibling session directory
    sibling_session_dir = tmp_path / SESSION_ROOT_DIRNAME / "session_other_uuid"
    sibling_session_dir.mkdir(parents=True)
    sibling_marker = sibling_session_dir / SESSION_MARKER_FILENAME
    sibling_marker.write_text("sibling", encoding="utf-8")

    session = Session(
        target_record=target_record,
        base_temp_dir=tmp_path,
    )
    with session:
        assert session.session_dir.is_dir()

    # After session cleanup:
    assert not session.session_dir.exists()
    # Unrelated files and sibling sessions must remain intact
    assert unrelated_file.is_file()
    assert sibling_session_dir.is_dir()
    assert sibling_marker.is_file()

