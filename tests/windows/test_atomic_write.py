"""Windows atomic file replacement and native backup tests.

Verifies:
1. Interactive confirmation ('y' applies, 'n' aborts without modifying target or creating backup).
2. Noninteractive --apply write authority (never bypasses validation, hash check, or safety gates).
3. Stale SHA-256 detection (aborts replacement when target modified externally).
4. Reparse point (symlink/junction) rejection before write.
5. Windows read-only attribute rejection before write.
6. Atomic replacement with native Win32 ReplaceFileW backup enabled (<target>.bak).
7. Atomic replacement without backup.
8. Replacement failure simulation (locked file): target unchanged, no corrupted backup.
9. Cross-volume enforcement: target and candidate/backup across different volumes fail closed.
10. Integration with PatchCandidate and same-volume staging.
11. Backup verification and restoration.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from localdev.errors import (
    PatchApplicationError,
    StaleEditError,
    TargetValidationError,
)
from localdev.patching.applier import PatchApplier
from localdev.patching.atomic_write import (
    AtomicReplacementResult,
    assert_same_volume,
    atomic_replace_file,
    get_same_volume_staging_dir,
    get_volume_for_path,
    prompt_confirmation,
    win32_replace_file,
)
from localdev.patching.backup import (
    get_backup_path,
    remove_backup,
    restore_backup,
    verify_backup,
)
from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)

pytestmark = [pytest.mark.windows]


class TestInteractiveConfirmationAndWriteAuthority:
    """Test interactive prompt confirmation and noninteractive --apply semantics."""

    def test_interactive_confirmation_yes_applies(self, tmp_path: Path) -> None:
        """Interactive confirmation 'y' grants write authority and replaces file."""
        target = tmp_path / "app.py"
        target.write_bytes(b"x = 1\n")
        baseline_sha256 = hashlib.sha256(b"x = 1\n").hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"x = 2\n")

        result = atomic_replace_file(
            target_path=target,
            candidate=candidate,
            baseline_sha256=baseline_sha256,
            has_write_authority=False,
            interactive=True,
            create_backup=False,
            prompt_func=lambda _p: True,
        )

        assert isinstance(result, AtomicReplacementResult)
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "x = 2\n"

    def test_win32_replace_file_direct(self, tmp_path: Path) -> None:
        """Low-level win32_replace_file replaces file and creates backup."""
        target = tmp_path / "low_level.py"
        target.write_text("v1\n", encoding="utf-8")
        repl = tmp_path / "low_level_new.py"
        repl.write_text("v2\n", encoding="utf-8")
        bak = tmp_path / "low_level.bak"

        win32_replace_file(target, repl, bak)
        assert target.read_text(encoding="utf-8") == "v2\n"
        assert bak.read_text(encoding="utf-8") == "v1\n"
        assert not repl.exists()

    def test_interactive_confirmation_no_aborts(self, tmp_path: Path) -> None:
        """Interactive confirmation 'n' aborts safely with zero modifications."""
        target = tmp_path / "app.py"
        target.write_bytes(b"x = 1\n")
        baseline_sha256 = hashlib.sha256(b"x = 1\n").hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"x = 2\n")

        result = atomic_replace_file(
            target_path=target,
            candidate=candidate,
            baseline_sha256=baseline_sha256,
            has_write_authority=False,
            interactive=True,
            create_backup=True,
            prompt_func=lambda _p: False,
        )

        assert result.success is False
        assert "aborted by user" in result.message.lower()
        # Target untouched
        assert target.read_bytes() == b"x = 1\n"
        # No backup created
        backup = get_backup_path(target)
        assert not backup.exists()

    def test_noninteractive_without_apply_flag_raises(self, tmp_path: Path) -> None:
        """Noninteractive execution without write authority raises PatchApplicationError."""
        target = tmp_path / "app.py"
        target.write_bytes(b"x = 1\n")
        baseline_sha256 = hashlib.sha256(b"x = 1\n").hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"x = 2\n")

        with pytest.raises(PatchApplicationError) as exc:
            atomic_replace_file(
                target_path=target,
                candidate=candidate,
                baseline_sha256=baseline_sha256,
                has_write_authority=False,
                interactive=False,
            )

        assert "Write authority required" in str(exc.value)

    def test_noninteractive_apply_flag_grants_authority(self, tmp_path: Path) -> None:
        """Noninteractive execution with has_write_authority=True (--apply) succeeds."""
        target = tmp_path / "app.py"
        target.write_bytes(b"x = 1\n")
        baseline_sha256 = hashlib.sha256(b"x = 1\n").hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"x = 100\n")

        result = atomic_replace_file(
            target_path=target,
            candidate=candidate,
            baseline_sha256=baseline_sha256,
            has_write_authority=True,
            interactive=False,
            create_backup=False,
        )

        assert result.success is True
        assert target.read_bytes() == b"x = 100\n"

    def test_prompt_confirmation_helper_variants(self) -> None:
        """prompt_confirmation helper correctly interprets y/yes/n/no inputs."""
        with patch("builtins.input", return_value="y"):
            assert prompt_confirmation() is True
        with patch("builtins.input", return_value="YES "):
            assert prompt_confirmation() is True
        with patch("builtins.input", return_value="n"):
            assert prompt_confirmation() is False
        with patch("builtins.input", return_value=""):
            assert prompt_confirmation() is False
        with patch("builtins.input", side_effect=EOFError):
            assert prompt_confirmation() is False


class TestPreWriteSafetyGatesAndStaleEdit:
    """Test stale SHA-256 detection, reparse point rejection, and read-only rejection."""

    def test_stale_sha256_detection_aborts_replacement(self, tmp_path: Path) -> None:
        """External modification to target file raises StaleEditError and aborts write."""
        target = tmp_path / "target.py"
        target.write_text("def foo():\n    return 1\n", encoding="utf-8")
        baseline_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()

        # Simulate external modification (e.g. user edits file in IDE before applying)
        target.write_text("def foo():\n    return 999  # Modified externally\n", encoding="utf-8")

        candidate = tmp_path / "candidate.py"
        candidate.write_text("def foo():\n    return 2\n", encoding="utf-8")

        with pytest.raises(StaleEditError) as exc:
            atomic_replace_file(
                target_path=target,
                candidate=candidate,
                baseline_sha256=baseline_sha256,
                has_write_authority=True,
                create_backup=True,
            )

        assert "modified externally" in str(exc.value)
        # Verify target retains external modifications and was not overwritten
        assert "return 999" in target.read_text(encoding="utf-8")
        # Verify no backup was created
        assert not get_backup_path(target).exists()

    def test_apply_flag_cannot_bypass_stale_sha256(self, tmp_path: Path) -> None:
        """--apply flag cannot bypass stale-edit detection; safety invariants remain enforced."""
        target = tmp_path / "target.py"
        target.write_bytes(b"a = 1\n")
        baseline_sha256 = hashlib.sha256(b"a = 1\n").hexdigest()

        # Modify file
        target.write_bytes(b"a = 2\n")

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"a = 3\n")

        # Even with has_write_authority=True, StaleEditError must be raised
        with pytest.raises(StaleEditError):
            atomic_replace_file(
                target_path=target,
                candidate=candidate,
                baseline_sha256=baseline_sha256,
                has_write_authority=True,
                interactive=False,
            )

    def test_reparse_point_rejection(self, tmp_path: Path) -> None:
        """Target identified as a reparse point / symlink is rejected before replacement."""
        target = tmp_path / "symlink_target.py"
        target.write_bytes(b"x = 1\n")
        baseline_sha256 = hashlib.sha256(b"x = 1\n").hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"x = 2\n")

        # Mock get_target_file_attributes to simulate a reparse point
        with patch("localdev.agent.permissions.get_target_file_attributes", return_value=(True, False)):
            with pytest.raises(TargetValidationError) as exc:
                atomic_replace_file(
                    target_path=target,
                    candidate=candidate,
                    baseline_sha256=baseline_sha256,
                    has_write_authority=True,
                )
            assert "symlink or reparse point" in str(exc.value)

    def test_read_only_target_rejection(self, tmp_path: Path) -> None:
        """Target with read-only attribute is rejected before replacement."""
        target = tmp_path / "readonly_target.py"
        target.write_bytes(b"x = 1\n")
        baseline_sha256 = hashlib.sha256(b"x = 1\n").hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"x = 2\n")

        with patch("localdev.agent.permissions.get_target_file_attributes", return_value=(False, True)):
            with pytest.raises(TargetValidationError) as exc:
                atomic_replace_file(
                    target_path=target,
                    candidate=candidate,
                    baseline_sha256=baseline_sha256,
                    has_write_authority=True,
                )
            assert "read-only attribute" in str(exc.value)


class TestNativeReplaceFileWAndBackup:
    """Test native Win32 ReplaceFileW atomic directory swap and native backup."""

    def test_atomic_replacement_with_native_backup(self, tmp_path: Path) -> None:
        """ReplaceFileW replaces target atomically and preserves original at <target>.bak."""
        target = tmp_path / "main.py"
        original_bytes = b"def original():\n    return 42\n"
        target.write_bytes(original_bytes)
        baseline_sha256 = hashlib.sha256(original_bytes).hexdigest()

        candidate = tmp_path / "candidate.py"
        new_bytes = b"def original():\n    return 100\n"
        candidate.write_bytes(new_bytes)

        backup_path = get_backup_path(target)
        if backup_path.exists():
            backup_path.unlink()

        result = atomic_replace_file(
            target_path=target,
            candidate=candidate,
            baseline_sha256=baseline_sha256,
            has_write_authority=True,
            create_backup=True,
        )

        assert result.success is True
        assert result.backup_created is True
        assert result.backup_path == backup_path

        # 1. Target file updated atomically
        assert target.read_bytes() == new_bytes

        # 2. Backup file preserves original pre-patch state
        assert backup_path.is_file()
        assert backup_path.read_bytes() == original_bytes
        verify_backup(backup_path, expected_sha256=baseline_sha256)

        # 3. Restoration from backup
        restore_backup(target, backup_path)
        assert target.read_bytes() == original_bytes

        # 4. Cleanup backup
        assert remove_backup(backup_path) is True
        assert not backup_path.exists()

    def test_atomic_replacement_without_backup(self, tmp_path: Path) -> None:
        """ReplaceFileW with create_backup=False passes NULL for backup parameter."""
        target = tmp_path / "script.py"
        target.write_bytes(b"count = 0\n")
        baseline_sha256 = hashlib.sha256(b"count = 0\n").hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"count = 10\n")

        result = atomic_replace_file(
            target_path=target,
            candidate=candidate,
            baseline_sha256=baseline_sha256,
            has_write_authority=True,
            create_backup=False,
        )

        assert result.success is True
        assert result.backup_created is False
        assert target.read_bytes() == b"count = 10\n"
        assert not get_backup_path(target).exists()

    def test_replacement_failure_on_locked_target(self, tmp_path: Path) -> None:
        """Target locked by external process fails ReplaceFileW; original remains untouched."""
        target = tmp_path / "locked.py"
        original_bytes = b"locked_content = True\n"
        target.write_bytes(original_bytes)
        baseline_sha256 = hashlib.sha256(original_bytes).hexdigest()

        candidate = tmp_path / "candidate.py"
        candidate.write_bytes(b"locked_content = False\n")

        backup = get_backup_path(target)

        # Lock the target file with exclusive read/write access
        with open(target, "r+", encoding="utf-8"):
            with pytest.raises(PatchApplicationError) as exc:
                atomic_replace_file(
                    target_path=target,
                    candidate=candidate,
                    baseline_sha256=baseline_sha256,
                    has_write_authority=True,
                    create_backup=True,
                )
            assert "ReplaceFileW failed" in str(exc.value)

        # Invariant checks:
        # 1. Target file was NOT modified
        assert target.read_bytes() == original_bytes
        # 2. No corrupted backup was left behind
        assert not backup.exists()


class TestSameVolumeEnforcementAndStaging:
    """Test same-volume enforcement and integration with PatchCandidate."""

    def test_cross_volume_rejection(self, tmp_path: Path) -> None:
        """Cross-volume candidate or backup path raises PatchApplicationError before ReplaceFileW."""
        target_path = Path("D:/project/app.py")
        temp_dir = Path(tempfile.gettempdir())  # Normally C:

        # If C: and D: are indeed different drives on this machine:
        if get_volume_for_path(target_path) != get_volume_for_path(temp_dir):
            candidate_c = temp_dir / "candidate_c.py"
            with pytest.raises(PatchApplicationError) as exc:
                assert_same_volume(target_path, candidate_c)
            assert "Cross-volume replacement prohibited" in str(exc.value)

    def test_assert_same_volume_success_on_same_drive(self, tmp_path: Path) -> None:
        """assert_same_volume succeeds for paths on the same volume."""
        p1 = tmp_path / "file1.py"
        p2 = tmp_path / "file2.py"
        assert_same_volume(p1, p2)

    def test_get_same_volume_staging_dir(self, tmp_path: Path) -> None:
        """get_same_volume_staging_dir creates staging directory on target's parent volume."""
        target = tmp_path / "app.py"
        staging = get_same_volume_staging_dir(target, session_id="test_session")
        assert staging.is_dir()
        assert get_volume_for_path(target) == get_volume_for_path(staging)
        assert ".localdev_staging" in str(staging)

    def test_integration_with_patch_candidate(self, tmp_path: Path) -> None:
        """Full flow: PatchCandidate applied to target using same-volume staging."""
        target_file = tmp_path / "calc.py"
        target_file.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        baseline_sha256 = hashlib.sha256(target_file.read_bytes()).hexdigest()

        # Build PatchCandidate via PatchApplier
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file=str(target_file),
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="    return a - b",
                    replacement_text="    return a + b",
                )
            ],
            explanation="Fix addition bug.",
        )

        candidate = applier.apply(proposal, target=target_file)

        # Apply atomically with backup
        result = atomic_replace_file(
            target_path=target_file,
            candidate=candidate,
            baseline_sha256=baseline_sha256,
            has_write_authority=True,
            create_backup=True,
        )

        assert result.success is True
        assert target_file.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"

        backup_file = get_backup_path(target_file)
        assert backup_file.is_file()
        assert backup_file.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
