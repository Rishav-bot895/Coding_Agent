"""Unit tests for patch applier and candidate generation.

Tests:
- Reverse line order application preventing line coordinate drift.
- Multi-chunk edits, insertions (start, middle, EOF, empty file), and deletions.
- Exact expected_text matching: mismatch aborts candidate generation immediately.
- File metadata preservation: CRLF, LF, UTF-8 BOM, and no-trailing-newline preservation.
- Absolute zero-mutation guarantee on disk: target file byte comparison before and after apply.
- Safe staging to dedicated directory and protection against overwriting target.
"""

from __future__ import annotations

import codecs
import hashlib
from pathlib import Path

import pytest

from localdev.errors import PatchApplicationError
from localdev.patching.applier import PatchApplier, PatchCandidate
from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.schemas import TargetRecord


def _make_target_record(
    path: Path,
    content_bytes: bytes,
    encoding: str = "utf-8",
    has_bom: bool = False,
    newline_style: str = "\n",
    has_trailing_newline: bool = True,
) -> TargetRecord:
    """Helper to create a TargetRecord for testing."""
    return TargetRecord(
        path=str(path),
        absolute_path=str(path.resolve()),
        file_size_bytes=len(content_bytes),
        sha256=hashlib.sha256(content_bytes).hexdigest(),
        encoding=encoding,
        has_bom=has_bom,
        newline_style=newline_style,  # type: ignore[arg-type]
        has_trailing_newline=has_trailing_newline,
        is_read_only=False,
        is_reparse_point=False,
    )


class TestPatchApplierOperations:
    """Test line-oriented edit operations: replace, insert, delete, and reverse ordering."""

    def test_single_line_replacement(self) -> None:
        """Replace a single line in a multi-line file."""
        code = "line1\nline2\nline3\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="line2",
                    replacement_text="line2_modified",
                )
            ],
            explanation="Update line 2.",
        )

        candidate = applier.apply(proposal, target="app.py", source_text=code)
        assert isinstance(candidate, PatchCandidate)
        assert candidate.patched_text == "line1\nline2_modified\nline3\n"
        assert candidate.line_count == 3
        assert candidate.changed_line_count == 1
        assert candidate.is_noop is False
        assert "-line2\n" in candidate.diff
        assert "+line2_modified\n" in candidate.diff

    def test_multiline_replacement_expansion(self) -> None:
        """Replace 2 lines with 4 lines (file expands)."""
        code = "def calc():\n    # TODO\n    pass\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=3,
                    expected_text="    # TODO\n    pass",
                    replacement_text="    a = 1\n    b = 2\n    return a + b",
                )
            ],
            explanation="Implement calc.",
        )

        candidate = applier.apply(proposal, target="app.py", source_text=code)
        expected = "def calc():\n    a = 1\n    b = 2\n    return a + b\n"
        assert candidate.patched_text == expected
        assert candidate.line_count == 4

    def test_insert_at_line_1(self) -> None:
        """Insertion before line 1 (start_line=1, end_line=0)."""
        code = "def foo():\n    return 42\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=1,
                    end_line=0,
                    expected_text="",
                    replacement_text="from __future__ import annotations\n",
                )
            ],
            explanation="Add annotations import.",
        )

        candidate = applier.apply(proposal, target="app.py", source_text=code)
        assert candidate.patched_text == (
            "from __future__ import annotations\ndef foo():\n    return 42\n"
        )
        assert candidate.line_count == 3

    def test_insert_in_middle(self) -> None:
        """Insertion before line K (start_line=K, end_line=K-1)."""
        code = "line1\nline2\nline3\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=2,
                    end_line=1,
                    expected_text="",
                    replacement_text="line1.5",
                )
            ],
            explanation="Insert line between 1 and 2.",
        )

        candidate = applier.apply(proposal, target="app.py", source_text=code)
        assert candidate.patched_text == "line1\nline1.5\nline2\nline3\n"

    def test_insert_at_eof(self) -> None:
        """EOF insertion (start_line=N+1, end_line=N)."""
        code = "line1\nline2\nline3\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=4,
                    end_line=3,
                    expected_text="",
                    replacement_text="line4\nline5",
                )
            ],
            explanation="Append lines at EOF.",
        )

        candidate = applier.apply(proposal, target="app.py", source_text=code)
        assert candidate.patched_text == "line1\nline2\nline3\nline4\nline5\n"

    def test_insert_in_empty_file(self) -> None:
        """Insertion into empty file (start_line=1, end_line=0)."""
        code = ""
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="empty.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=1,
                    end_line=0,
                    expected_text="",
                    replacement_text="new_initial_line\n",
                )
            ],
            explanation="Populate empty file.",
        )

        candidate = applier.apply(proposal, target="empty.py", source_text=code)
        assert candidate.patched_text == "new_initial_line\n"
        assert candidate.line_count == 1

    def test_delete_single_line(self) -> None:
        """Delete single line from middle."""
        code = "line1\nline2_del\nline3\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.DELETE,
                    start_line=2,
                    end_line=2,
                    expected_text="line2_del",
                    replacement_text="",
                )
            ],
            explanation="Delete line 2.",
        )

        candidate = applier.apply(proposal, target="app.py", source_text=code)
        assert candidate.patched_text == "line1\nline3\n"

    def test_delete_all_lines(self) -> None:
        """Delete all lines from file resulting in empty file."""
        code = "line1\nline2\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.DELETE,
                    start_line=1,
                    end_line=2,
                    expected_text="line1\nline2",
                    replacement_text="",
                )
            ],
            explanation="Delete everything.",
        )

        candidate = applier.apply(proposal, target="app.py", source_text=code)
        assert candidate.patched_text == ""
        assert candidate.line_count == 0

    def test_multi_chunk_edits_reverse_line_order(self) -> None:
        """Multi-chunk edits: expansions and deletions earlier/later do not shift line numbers."""
        code = (
            "alpha = 1\n"
            "beta = 2\n"
            "gamma = 3\n"
            "delta = 4\n"
            "epsilon = 5\n"
            "zeta = 6\n"
        )
        applier = PatchApplier()

        proposal = EditProposalRecord(
            target_file="vars.py",
            edits=[
                # Edit 0: replace line 2 with 3 lines (expansion)
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="beta = 2",
                    replacement_text="beta_1 = 20\nbeta_2 = 21\nbeta_3 = 22",
                ),
                # Edit 1: delete line 4
                EditOperation(
                    operation=EditOperationType.DELETE,
                    start_line=4,
                    end_line=4,
                    expected_text="delta = 4",
                    replacement_text="",
                ),
                # Edit 2: replace line 6
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=6,
                    end_line=6,
                    expected_text="zeta = 6",
                    replacement_text="zeta = 600",
                ),
            ],
            explanation="Multiple non-contiguous modifications.",
        )

        candidate = applier.apply(proposal, target="vars.py", source_text=code)
        expected = (
            "alpha = 1\n"
            "beta_1 = 20\n"
            "beta_2 = 21\n"
            "beta_3 = 22\n"
            "gamma = 3\n"
            "epsilon = 5\n"
            "zeta = 600\n"
        )
        assert candidate.patched_text == expected


class TestExpectedTextMatchingAndSafety:
    """Test exact expected_text matching as primary defense against stale or hallucinated offsets."""

    def test_mismatched_content_aborts_candidate_generation(self) -> None:
        """Content mismatch raises PatchApplicationError and aborts candidate creation."""
        code = "def foo():\n    return 41\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="    return 999",  # Does NOT match 41
                    replacement_text="    return 42",
                )
            ],
            explanation="Fix return.",
        )

        with pytest.raises(PatchApplicationError) as exc:
            applier.apply(proposal, target="app.py", source_text=code)

        assert "expected_text mismatch" in str(exc.value)

    def test_mismatched_indentation_aborts_candidate_generation(self) -> None:
        """Indentation mismatch raises PatchApplicationError."""
        code = "def foo():\n    return 42\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="  return 42",  # 2 spaces instead of 4
                    replacement_text="    return 100",
                )
            ],
            explanation="Change return.",
        )

        with pytest.raises(PatchApplicationError) as exc:
            applier.apply(proposal, target="app.py", source_text=code)

        assert "expected_text mismatch" in str(exc.value)

    def test_multi_chunk_later_mismatch_aborts_entire_proposal(self) -> None:
        """If edit 2 of 3 has expected_text mismatch, entire generation aborts (all-or-nothing)."""
        code = "line1\nline2\nline3\n"
        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="line1",
                    replacement_text="line1_new",
                ),
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=3,
                    end_line=3,
                    expected_text="wrong_line3",  # Mismatch!
                    replacement_text="line3_new",
                ),
            ],
            explanation="Update 1 and 3.",
        )

        with pytest.raises(PatchApplicationError):
            applier.apply(proposal, target="app.py", source_text=code)


class TestFileMetadataPreservation:
    """Test preservation of CRLF, UTF-8 BOM, and no-trailing-newline states."""

    def test_crlf_preservation(self) -> None:
        """CRLF target preserves CRLF across all reconstructed lines even if model outputs LF."""
        raw = b"def foo():\r\n    return 42\r\n"
        target_path = Path("crlf_target.py")
        target = _make_target_record(
            path=target_path,
            content_bytes=raw,
            newline_style="\r\n",
            has_trailing_newline=True,
        )

        applier = PatchApplier()
        # Model outputs replacement text with standard \n
        proposal = EditProposalRecord(
            target_file=str(target_path),
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="    return 42",
                    replacement_text="    val = 100\n    return val",
                )
            ],
            explanation="Expand function with CRLF preservation.",
        )

        candidate = applier.apply(proposal, target=target, raw_bytes=raw)

        # Verify patched_text and patched_bytes use \r\n
        assert "\r\n" in candidate.patched_text
        assert "\r\n" in candidate.patched_bytes.decode("utf-8")
        assert candidate.patched_bytes == (
            b"def foo():\r\n    val = 100\r\n    return val\r\n"
        )
        assert candidate.has_trailing_newline is True
        assert candidate.newline_style == "\r\n"

    def test_utf8_bom_preservation(self) -> None:
        """Target with UTF-8 BOM retains BOM in patched_bytes."""
        raw = codecs.BOM_UTF8 + b"x = 1\ny = 2\n"
        target_path = Path("bom_target.py")
        target = _make_target_record(
            path=target_path,
            content_bytes=raw,
            encoding="utf-8",
            has_bom=True,
            newline_style="\n",
            has_trailing_newline=True,
        )

        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file=str(target_path),
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="x = 1",
                    replacement_text="x = 99",
                )
            ],
            explanation="Update x.",
        )

        candidate = applier.apply(proposal, target=target, raw_bytes=raw)

        assert candidate.has_bom is True
        assert candidate.patched_bytes.startswith(codecs.BOM_UTF8)
        assert candidate.patched_bytes == codecs.BOM_UTF8 + b"x = 99\ny = 2\n"

    def test_non_bom_target_does_not_acquire_bom(self) -> None:
        """Target without BOM does NOT gain BOM in patched_bytes."""
        raw = b"x = 1\n"
        target_path = Path("plain.py")
        target = _make_target_record(
            path=target_path,
            content_bytes=raw,
            encoding="utf-8",
            has_bom=False,
            newline_style="\n",
            has_trailing_newline=True,
        )

        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file=str(target_path),
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="x = 1",
                    replacement_text="x = 2",
                )
            ],
            explanation="Update x.",
        )

        candidate = applier.apply(proposal, target=target, raw_bytes=raw)
        assert candidate.has_bom is False
        assert not candidate.patched_bytes.startswith(codecs.BOM_UTF8)

    def test_no_trailing_newline_preservation(self) -> None:
        """Target file lacking a trailing newline preserves no-trailing-newline state."""
        raw = b"a = 1\nb = 2"  # No trailing newline
        target_path = Path("no_newline.py")
        target = _make_target_record(
            path=target_path,
            content_bytes=raw,
            has_trailing_newline=False,
        )

        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file=str(target_path),
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="a = 1",
                    replacement_text="a = 10",
                )
            ],
            explanation="Update line 1 without adding trailing newline.",
        )

        candidate = applier.apply(proposal, target=target, raw_bytes=raw)

        assert candidate.has_trailing_newline is False
        assert not candidate.patched_text.endswith(("\n", "\r"))
        assert candidate.patched_bytes == b"a = 10\nb = 2"


class TestZeroDiskModificationAndStaging:
    """Test absolute guarantee of zero target disk mutation and safe staging."""

    def test_target_file_on_disk_is_never_modified(self, tmp_path: Path) -> None:
        """Applying edits never mutates the original file on disk (verified by SHA-256 byte check)."""
        target_file = tmp_path / "protected_target.py"
        original_content = b"def original():\n    return 'untouched'\n"
        target_file.write_bytes(original_content)
        original_sha256 = hashlib.sha256(original_content).hexdigest()

        target = _make_target_record(
            path=target_file,
            content_bytes=original_content,
        )

        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file=str(target_file),
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="    return 'untouched'",
                    replacement_text="    return 'patched'",
                )
            ],
            explanation="Propose patch.",
        )

        candidate = applier.apply(proposal, target=target)

        # 1. Candidate reflects the proposed changes
        assert candidate.patched_text == "def original():\n    return 'patched'\n"

        # 2. Disk file remains 100% UNTOUCHED
        disk_bytes_after = target_file.read_bytes()
        disk_sha256_after = hashlib.sha256(disk_bytes_after).hexdigest()
        assert disk_bytes_after == original_content
        assert disk_sha256_after == original_sha256

    def test_write_to_staging_and_collision_protection(self, tmp_path: Path) -> None:
        """Candidate can be staged to a directory and forbids overwriting target directly."""
        target_file = tmp_path / "target.py"
        target_file.write_text("x = 1\n", encoding="utf-8")

        target = _make_target_record(
            path=target_file,
            content_bytes=b"x = 1\n",
        )

        applier = PatchApplier()
        proposal = EditProposalRecord(
            target_file=str(target_file),
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="x = 1",
                    replacement_text="x = 2",
                )
            ],
            explanation="Update x.",
        )

        candidate = applier.apply(proposal, target=target)

        # Staging directory on same volume
        staging_dir = tmp_path / "staging"
        staged_path = candidate.write_to_staging(staging_dir)

        assert staged_path.is_file()
        assert staged_path.read_text(encoding="utf-8") == "x = 2\n"
        assert staged_path.name == "candidate_target.py"

        # Refuse to stage over the target directly
        with pytest.raises(PatchApplicationError) as exc:
            candidate.write_to_staging(staging_dir=tmp_path, filename="target.py")
        assert "cannot overwrite the original target file" in str(exc.value)

        # Refuse direct write_to_path without allow_target_overwrite
        with pytest.raises(PatchApplicationError) as exc2:
            candidate.write_to_path(target_file)
        assert "Direct overwrite of original target file forbidden" in str(exc2.value)
