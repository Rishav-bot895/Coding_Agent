"""Integration tests for the strict 5-stage handle and process cleanup sequence (P2-T3).

Verifies reliable cleanup across normal execution, exception paths, simulated
interruptions (KeyboardInterrupt), and child processes holding open file handles
without Windows file-locking failures (ERROR_SHARING_VIOLATION).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from localdev.agent.permissions import validate_target
from localdev.agent.session import Session

BOUNDARY_SAMPLES_DIR = Path(__file__).parent.parent / "boundary_samples"


def test_standard_session_clean_lifecycle(tmp_path: Path) -> None:
    """Session initializes and completely cleans up temporary directories upon exit."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(target_record=target_record, base_temp_dir=tmp_path)
    session_dir = session.session_dir

    with session:
        assert session_dir.is_dir()
        assert (session_dir / "session_target.py").is_file()

    assert not session_dir.exists()


def test_session_cleanup_on_exception(tmp_path: Path) -> None:
    """When an exception occurs within the session context, cleanup still executes."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(target_record=target_record, base_temp_dir=tmp_path)
    session_dir = session.session_dir

    with pytest.raises(RuntimeError, match="Simulated failure inside session"), session:
        assert session_dir.is_dir()
        raise RuntimeError("Simulated failure inside session")

    # Session directory must be cleaned up despite the exception
    assert not session_dir.exists()


def test_session_cleanup_on_keyboard_interrupt(tmp_path: Path) -> None:
    """When KeyboardInterrupt occurs, cleanup sequence runs and deletes session dir."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(target_record=target_record, base_temp_dir=tmp_path)
    session_dir = session.session_dir

    with pytest.raises(KeyboardInterrupt), session:
        assert session_dir.is_dir()
        raise KeyboardInterrupt()

    assert not session_dir.exists()


@pytest.mark.windows
def test_5_stage_cleanup_with_open_file_process(tmp_path: Path) -> None:
    """Process holding open file handle is terminated and waited for before directory deletion.

    On Windows, attempting to delete a directory containing a file held open by a running
    process raises PermissionError (ERROR_SHARING_VIOLATION). The 5-stage cleanup sequence
    guarantees termination, pipe closure, wait, and handle closure before directory deletion.
    """
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(target_record=target_record, base_temp_dir=tmp_path)
    session.initialize()

    # Launch a child process that opens a file in the session directory and holds it open
    lock_file = session.session_dir / "locked_file.bin"
    lock_file.write_bytes(b"initial data")

    # Child script opens file in read-write mode and sleeps
    child_code = (
        "import time\n"
        f"f = open(r'{lock_file}', 'r+b')\n"
        "time.sleep(30)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Register process with session
    session.register_process(proc)

    # Allow child process to start and acquire the file handle
    import time
    time.sleep(0.5)

    # Verify process is running
    assert proc.poll() is None

    # Execute 5-stage cleanup: terminates proc, waits for exit, closes handles, deletes dir
    session.cleanup()

    # Verify process was terminated
    assert proc.poll() is not None

    # Verify session directory was deleted cleanly without ERROR_SHARING_VIOLATION
    assert not session.session_dir.exists()


def test_cleanup_with_both_session_and_staging_dirs(tmp_path: Path) -> None:
    """Cleanup deletes both general session directory and same-volume staging directory."""
    sample_target = BOUNDARY_SAMPLES_DIR / "utf8_plain.py"
    target_record = validate_target(sample_target)

    session = Session(target_record=target_record, base_temp_dir=tmp_path)
    session.initialize()

    staging_dir = session.create_same_volume_staging_dir()
    candidate_file = staging_dir / "candidate.py"
    candidate_file.write_bytes(b"print('candidate')\n")

    assert session.session_dir.is_dir()
    assert staging_dir.is_dir()
    assert candidate_file.is_file()

    session.cleanup()

    assert not session.session_dir.exists()
    assert not staging_dir.exists()
