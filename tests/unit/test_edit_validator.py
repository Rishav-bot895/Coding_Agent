"""Unit tests for structured edit schema and edit validator.

Tests the frozen indexing semantics and safety bounds from Phase 8 (P8-T1):
- 1-based, inclusive line indexing.
- Valid replace, insert at line 1, insert at EOF, delete lines, and empty file insert.
- Overlapping ranges, out-of-order ranges, and contiguous ambiguous edits.
- Mismatched expected_text (content, indentation, line count).
- Max 8 edits and max 80 changed lines limits.
- Target path mismatch, traversal attempts, and shell metacharacter injection.
- Line ending normalization (CRLF target vs LF proposal) and trailing newlines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from localdev.constants import MAX_PATCH_CHANGED_LINES
from localdev.errors import EditValidationError
from localdev.inference.prompts import build_edit_proposal_correction_prompt
from localdev.inference.response_validator import EditProposalValidator
from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.patching.edit_validator import EditValidator
from localdev.schemas import TargetRecord


def _make_dummy_target(path: str = "app.py", size: int = 100) -> TargetRecord:
    return TargetRecord(
        path=path,
        absolute_path=str(Path(path).resolve()),
        file_size_bytes=size,
        sha256="a" * 64,
        encoding="utf-8",
        has_bom=False,
        newline_style="\n",
        has_trailing_newline=True,
        is_read_only=False,
        is_reparse_point=False,
    )


# =============================================================================
# 1. Valid Edit Operations and Indexing Semantics
# =============================================================================


class TestValidEditOperations:
    """Test valid replace, insert at line 1, insert at EOF, delete lines, and empty file insert."""

    def test_valid_replace_single_line(self) -> None:
        """Valid replacement of a single line in a multi-line file."""
        code = "def foo():\n    return 41\n"
        target = _make_dummy_target("app.py", len(code))
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="    return 41",
                    replacement_text="    return 42",
                )
            ],
            explanation="Fix return value to 42.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 1
        validator.validate(proposal)

    def test_valid_replace_multiline(self) -> None:
        """Valid replacement of multiple lines."""
        code = "def calc(a, b):\n    # Old logic\n    temp = a + b\n    return temp\n"
        target = _make_dummy_target("calc.py", len(code))
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="calc.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=4,
                    expected_text="    # Old logic\n    temp = a + b\n    return temp",
                    replacement_text="    return a + b",
                )
            ],
            explanation="Inline return expression.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 3

    def test_valid_multiple_ordered_edits(self) -> None:
        """Multiple non-overlapping edits ordered strictly by increasing line numbers."""
        code = "x = 1\ny = 2\nz = 3\nw = 4\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="x = 1",
                    replacement_text="x = 10",
                ),
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=3,
                    end_line=4,
                    expected_text="z = 3\nw = 4",
                    replacement_text="z = 30\nw = 40",
                ),
            ],
            explanation="Update x, z, and w.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 3

    def test_insert_at_line_1(self) -> None:
        """Insertion before line 1: start_line = 1, end_line = 0, expected_text = ''."""
        code = "def foo():\n    return 42\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=1,
                    end_line=0,
                    expected_text="",
                    replacement_text="import os\n",
                )
            ],
            explanation="Add os import at top of file.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 1

    def test_insert_at_eof(self) -> None:
        """EOF insertion (append): on an N-line file, start_line = N + 1, end_line = N."""
        code = "line1\nline2\nline3\n"  # 3 lines
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=4,
                    end_line=3,
                    expected_text="",
                    replacement_text="line4\n",
                )
            ],
            explanation="Append line4 at EOF.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 1

    def test_insert_in_middle(self) -> None:
        """Insertion before line K: start_line = K, end_line = K - 1."""
        code = "line1\nline2\nline3\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        # Insert before line 2 (between line 1 and line 2)
        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=2,
                    end_line=1,
                    expected_text="",
                    replacement_text="line1_point_5\n",
                )
            ],
            explanation="Insert line between 1 and 2.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 1

    def test_delete_lines(self) -> None:
        """Deletion: start_line <= end_line, expected_text matches, replacement_text = ''."""
        code = "line1\nline2_to_delete\nline3\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.DELETE,
                    start_line=2,
                    end_line=2,
                    expected_text="line2_to_delete",
                    replacement_text="",
                )
            ],
            explanation="Remove unnecessary line 2.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 1

    def test_empty_file_insert(self) -> None:
        """Empty file (0 lines): insertion at start_line = 1, end_line = 0."""
        code = ""  # 0 lines
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=1,
                    end_line=0,
                    expected_text="",
                    replacement_text="print('init')\n",
                )
            ],
            explanation="Populate empty file.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []
        assert result.total_changed_lines == 1


# =============================================================================
# 2. Rejection of Invalid Indexing, Overlap, and Out-of-Order Ranges
# =============================================================================


class TestInvalidIndexingAndOrdering:
    """Test rejection of overlapping ranges, out-of-order ranges, and invalid indexing."""

    def test_overlapping_replace_ranges_rejected(self) -> None:
        """Edits overlapping at line intervals must be rejected."""
        with pytest.raises(ValidationError) as exc:
            EditProposalRecord(
                target_file="app.py",
                edits=[
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=2,
                        end_line=5,
                        expected_text="a\nb\nc\nd",
                        replacement_text="new",
                    ),
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=4,
                        end_line=7,
                        expected_text="c\nd\ne\nf",
                        replacement_text="new2",
                    ),
                ],
                explanation="Overlapping edits.",
            )
        assert "strictly ordered by increasing line numbers without overlap" in str(exc.value)

    def test_overlapping_at_boundary_line_rejected(self) -> None:
        """Edits sharing a boundary line (e.g. 1-3 and 3-5) are overlapping and rejected."""
        with pytest.raises(ValidationError) as exc:
            EditProposalRecord(
                target_file="app.py",
                edits=[
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=1,
                        end_line=3,
                        expected_text="a\nb\nc",
                        replacement_text="new",
                    ),
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=3,
                        end_line=5,
                        expected_text="c\nd\ne",
                        replacement_text="new2",
                    ),
                ],
                explanation="Boundary overlap.",
            )
        assert "strictly ordered by increasing line numbers without overlap" in str(exc.value)

    def test_out_of_order_edits_rejected(self) -> None:
        """Edits specified in reversed line order must be rejected."""
        with pytest.raises(ValidationError) as exc:
            EditProposalRecord(
                target_file="app.py",
                edits=[
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=10,
                        end_line=12,
                        expected_text="a\nb\nc",
                        replacement_text="new1",
                    ),
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=2,
                        end_line=4,
                        expected_text="d\ne\nf",
                        replacement_text="new2",
                    ),
                ],
                explanation="Out-of-order edits.",
            )
        assert "strictly ordered by increasing line numbers without overlap" in str(exc.value)

    def test_contiguous_ambiguous_insertions_at_same_line_rejected(self) -> None:
        """Two insertions at the exact same line position are contiguous and ambiguous."""
        with pytest.raises(ValidationError) as exc:
            EditProposalRecord(
                target_file="app.py",
                edits=[
                    EditOperation(
                        operation=EditOperationType.INSERT,
                        start_line=5,
                        end_line=4,
                        expected_text="",
                        replacement_text="insert1\n",
                    ),
                    EditOperation(
                        operation=EditOperationType.INSERT,
                        start_line=5,
                        end_line=4,
                        expected_text="",
                        replacement_text="insert2\n",
                    ),
                ],
                explanation="Duplicate insertions at same line.",
            )
        assert "strictly ordered by increasing line numbers without overlap" in str(exc.value)

    def test_insert_and_replace_at_same_start_line_rejected(self) -> None:
        """Insertion before line K followed immediately by replacement at line K is ambiguous."""
        with pytest.raises(ValidationError) as exc:
            EditProposalRecord(
                target_file="app.py",
                edits=[
                    EditOperation(
                        operation=EditOperationType.INSERT,
                        start_line=5,
                        end_line=4,
                        expected_text="",
                        replacement_text="new_code\n",
                    ),
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=5,
                        end_line=6,
                        expected_text="old5\nold6",
                        replacement_text="new5_6\n",
                    ),
                ],
                explanation="Insert and replace at line 5.",
            )
        assert "strictly ordered by increasing line numbers without overlap" in str(exc.value)

    def test_reversed_start_end_lines_rejected(self) -> None:
        """start_line > end_line in replace or delete is rejected."""
        with pytest.raises(ValidationError) as exc:
            EditOperation(
                operation=EditOperationType.REPLACE,
                start_line=10,
                end_line=5,
                expected_text="foo",
                replacement_text="bar",
            )
        assert "start_line (10) <= end_line (5)" in str(exc.value)

    def test_invalid_insert_indexing_rejected(self) -> None:
        """Insert operation must satisfy start_line == end_line + 1."""
        with pytest.raises(ValidationError) as exc:
            EditOperation(
                operation=EditOperationType.INSERT,
                start_line=5,
                end_line=5,
                expected_text="",
                replacement_text="foo",
            )
        assert "insert operation requires start_line == end_line + 1" in str(exc.value)

    def test_insert_with_non_empty_expected_text_rejected(self) -> None:
        """Insert operation requires expected_text == ''."""
        with pytest.raises(ValidationError) as exc:
            EditOperation(
                operation=EditOperationType.INSERT,
                start_line=5,
                end_line=4,
                expected_text="not empty",
                replacement_text="foo",
            )
        assert "insert operation requires expected_text to be empty" in str(exc.value)

    def test_delete_with_non_empty_replacement_text_rejected(self) -> None:
        """Delete operation requires replacement_text == ''."""
        with pytest.raises(ValidationError) as exc:
            EditOperation(
                operation=EditOperationType.DELETE,
                start_line=5,
                end_line=5,
                expected_text="to delete",
                replacement_text="should be empty",
            )
        assert "delete operation requires replacement_text to be empty" in str(exc.value)


# =============================================================================
# 3. Mismatched Expected Text and Bounds Verification
# =============================================================================


class TestExpectedTextAndBoundsVerification:
    """Test exact matching of expected_text and line bounds checking against target source."""

    def test_mismatched_expected_text_content(self) -> None:
        """Mismatch in expected_text content is detected and reported."""
        code = "x = 1\ny = 2\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="x = 99",  # actual is 'x = 1'
                    replacement_text="x = 10",
                )
            ],
            explanation="Update x.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("expected_text mismatch at line 1" in err for err in result.errors)

    def test_mismatched_expected_text_indentation(self) -> None:
        """Indentation mismatch is strictly caught and rejected."""
        code = "def foo():\n    return 1\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="  return 1",  # 2 spaces instead of 4
                    replacement_text="    return 2",
                )
            ],
            explanation="Fix return.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("expected_text mismatch at line 2" in err for err in result.errors)

    def test_mismatched_expected_line_count(self) -> None:
        """Expected text line count not matching line span [start_line, end_line] is rejected."""
        code = "line1\nline2\nline3\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,  # 1 line span
                    expected_text="line1\nline2",  # 2 lines supplied
                    replacement_text="new_line",
                )
            ],
            explanation="Mismatch count.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("spans 1 lines" in err for err in result.errors)

    def test_replace_beyond_eof_rejected(self) -> None:
        """Replacing lines beyond target file EOF is rejected."""
        code = "line1\nline2\n"  # 2 lines
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=3,
                    end_line=3,
                    expected_text="line3",
                    replacement_text="new_line",
                )
            ],
            explanation="Beyond EOF.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("exceeds target file line count (2)" in err for err in result.errors)

    def test_insert_beyond_eof_plus_one_rejected(self) -> None:
        """Inserting at line > total_lines + 1 is rejected."""
        code = "line1\nline2\n"  # 2 lines; max insert is line 3 (EOF)
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=4,
                    end_line=3,
                    expected_text="",
                    replacement_text="line4",
                )
            ],
            explanation="Beyond EOF+1.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("exceeds maximum allowed insertion position (3)" in err for err in result.errors)

    def test_empty_file_replace_or_delete_rejected(self) -> None:
        """Replace or delete operations on empty file are rejected."""
        code = ""
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.DELETE,
                    start_line=1,
                    end_line=1,
                    expected_text="something",
                    replacement_text="",
                )
            ],
            explanation="Cannot delete in empty file.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("cannot delete on an empty file" in err for err in result.errors)

    def test_empty_file_invalid_insert_line_rejected(self) -> None:
        """Empty file only allows insert at start_line=1, end_line=0."""
        code = ""
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=2,
                    end_line=1,
                    expected_text="",
                    replacement_text="new code",
                )
            ],
            explanation="Invalid insert coordinate on empty file.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("empty file insertion requires start_line=1, end_line=0" in err for err in result.errors)


# =============================================================================
# 4. Complexity Bounds and Limits
# =============================================================================


class TestEditComplexityBounds:
    """Test enforcement of max 8 edits and max 80 total changed lines."""

    def test_exceeding_max_edits_rejected(self) -> None:
        """Proposals with > 8 edits fail Pydantic schema validation."""
        too_many = [
            EditOperation(
                operation=EditOperationType.REPLACE,
                start_line=i * 2 + 1,
                end_line=i * 2 + 1,
                expected_text="x",
                replacement_text="y",
            )
            for i in range(9)
        ]
        with pytest.raises(ValidationError):
            EditProposalRecord(
                target_file="app.py",
                edits=too_many,
                explanation="Too many edits.",
            )

    def test_exceeding_max_changed_lines_rejected(self) -> None:
        """Proposals with > 80 changed lines fail validation."""
        huge_edit = EditOperation(
            operation=EditOperationType.REPLACE,
            start_line=1,
            end_line=85,
            expected_text="\n".join(f"line_{i}" for i in range(1, 86)),
            replacement_text="\n".join(f"new_{i}" for i in range(1, 86)),
        )
        with pytest.raises(ValidationError) as exc:
            EditProposalRecord(
                target_file="app.py",
                edits=[huge_edit],
                explanation="Too many changed lines.",
            )
        assert f"exceeds hard limit of {MAX_PATCH_CHANGED_LINES}" in str(exc.value)


# =============================================================================
# 5. Target Path Security and Path Traversal Defense
# =============================================================================


class TestTargetPathSecurity:
    """Test rejection of path traversal attempts, shell metacharacters, and mismatched targets."""

    @pytest.mark.parametrize(
        "bad_path",
        [
            "../secret.py",
            "..\\secret.py",
            "sub/../../secret.py",
            "app.py; rm -rf /",
            "app.py | cat",
            "app.py & calc.exe",
            "`whoami`.py",
            "$HOME/secret.py",
            "app.py > out.txt",
        ],
    )
    def test_dangerous_target_paths_rejected_by_schema(self, bad_path: str) -> None:
        """Path traversal and shell metacharacters rejected at schema validation."""
        with pytest.raises(ValidationError):
            EditProposalRecord(
                target_file=bad_path,
                edits=[
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=1,
                        end_line=1,
                        expected_text="x",
                        replacement_text="y",
                    )
                ],
                explanation="Dangerous path attempt.",
            )

    def test_target_file_mismatch_rejected_by_validator(self) -> None:
        """Proposal targeting a different file than the session target is rejected."""
        code = "x = 1\n"
        target = _make_dummy_target("correct_script.py")
        validator = EditValidator(target=target, source_text=code)

        proposal = EditProposalRecord(
            target_file="wrong_script.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=1,
                    end_line=1,
                    expected_text="x = 1",
                    replacement_text="x = 2",
                )
            ],
            explanation="Mismatch target file name.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is False
        assert any("Target file mismatch" in err for err in result.errors)

        with pytest.raises(EditValidationError) as exc:
            validator.validate(proposal)
        assert "Target file mismatch" in str(exc.value)


# =============================================================================
# 6. Line Ending Normalization and Response Validator Integration
# =============================================================================


class TestLineEndingNormalizationAndResponseValidator:
    """Test CRLF vs LF normalization, trailing newlines, and EditProposalValidator."""

    def test_crlf_source_matches_lf_proposal(self) -> None:
        """Target file with CRLF line endings matches expected_text normalized with LF."""
        code_crlf = "def foo():\r\n    return 42\r\n"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code_crlf)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="    return 42",
                    replacement_text="    return 100",
                )
            ],
            explanation="CRLF line ending matching.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []

    def test_source_without_trailing_newline_matches(self) -> None:
        """Target file without a trailing newline is handled cleanly."""
        code_no_newline = "x = 1\ny = 2"
        target = _make_dummy_target()
        validator = EditValidator(target=target, source_text=code_no_newline)

        proposal = EditProposalRecord(
            target_file="app.py",
            edits=[
                EditOperation(
                    operation=EditOperationType.REPLACE,
                    start_line=2,
                    end_line=2,
                    expected_text="y = 2",
                    replacement_text="y = 20",
                )
            ],
            explanation="No trailing newline in source.",
        )

        result = validator.validate_proposal(proposal)
        assert result.is_valid is True
        assert result.errors == []

    def test_edit_proposal_validator_raw_payload_valid(self) -> None:
        """EditProposalValidator parses and validates a valid JSON string payload."""
        code = "def add(a, b):\n    return a - b\n"
        target = _make_dummy_target("math_utils.py")
        resp_validator = EditProposalValidator(target=target, source_text=code)

        payload = json.dumps(
            {
                "target_file": "math_utils.py",
                "edits": [
                    {
                        "operation": "replace",
                        "start_line": 2,
                        "end_line": 2,
                        "expected_text": "    return a - b",
                        "replacement_text": "    return a + b",
                    }
                ],
                "explanation": "Fix addition bug.",
            }
        )

        record, errors = resp_validator.validate_payload(payload)
        assert record is not None
        assert errors == []
        assert record.target_file == "math_utils.py"

    def test_edit_proposal_validator_malformed_json(self) -> None:
        """EditProposalValidator rejects malformed JSON."""
        resp_validator = EditProposalValidator(target=_make_dummy_target(), source_text="x = 1\n")
        record, errors = resp_validator.validate_payload("Not JSON {")
        assert record is None
        assert any("Malformed JSON syntax" in err for err in errors)

    def test_edit_proposal_validator_empty_payload(self) -> None:
        """EditProposalValidator rejects empty payload."""
        resp_validator = EditProposalValidator(target=_make_dummy_target(), source_text="x = 1\n")
        record, errors = resp_validator.validate_payload("   ")
        assert record is None
        assert any("empty or whitespace" in err for err in errors)

    def test_edit_proposal_validator_shape_mismatch(self) -> None:
        """EditProposalValidator rejects JSON list when object expected."""
        resp_validator = EditProposalValidator(target=_make_dummy_target(), source_text="x = 1\n")
        record, errors = resp_validator.validate_payload("[1, 2, 3]")
        assert record is None
        assert any("Expected JSON object (dict)" in err for err in errors)

    def test_edit_proposal_correction_prompt_formatting(self) -> None:
        """Correction prompt includes error bullets and reminder instructions."""
        errors = [
            "expected_text mismatch at line 2",
            "Edits 0 and 1 are overlapping",
        ]
        prompt = build_edit_proposal_correction_prompt(errors, '{"invalid": true}')
        assert "expected_text mismatch at line 2" in prompt
        assert "Edits 0 and 1 are overlapping" in prompt
        assert "1-based and inclusive" in prompt
        assert "Maximum 8 edits and 80 total changed lines" in prompt
