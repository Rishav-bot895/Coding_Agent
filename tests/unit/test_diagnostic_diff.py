"""Unit tests for differential patch validation (Validation Level A - Static validity).

Verifies:
1. Line coordinate shift calculations (compute_line_shift_before, map_baseline_line_to_candidate, is_candidate_line_modified).
2. Diagnostic multi-set diffing and tolerance of pre-existing findings across shifted lines.
3. Elimination of targeted static diagnostics.
4. Validation Level A certification for runtime-only repairs on Ruff-clean baselines.
5. Rejection of candidate patches introducing new syntax errors or new static warnings.
6. Integration with PythonAdapter.validate_candidate and evidence collection.
"""

from __future__ import annotations

from pathlib import Path

from localdev.agent.evidence import evaluate_candidate_level_a
from localdev.languages.python.adapter import PythonAdapter
from localdev.languages.python.diagnostics import (
    DiagnosticDiffResult,
    LevelAResult,
    compare_diagnostics,
    evaluate_level_a,
)
from localdev.patching.applier import (
    PatchApplier,
    compute_line_shift_before,
    is_candidate_line_modified,
    map_baseline_line_to_candidate,
)
from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.schemas import (
    AnalysisReport,
    DiagnosticRecord,
    SeverityEnum,
    TargetRecord,
    ValidationLevel,
)


def _make_diagnostic(
    code: str = "F401",
    line: int = 1,
    col: int = 1,
    message: str = "Unused import",
    source: str = "ruff",
    severity: SeverityEnum = SeverityEnum.WARNING,
) -> DiagnosticRecord:
    """Helper to construct a valid DiagnosticRecord."""
    return DiagnosticRecord(
        source=source,
        code=code,
        message=message,
        severity=severity,
        start_line=line,
        start_col=col,
        end_line=line,
        end_col=col + 5,
        fix_available=False,
    )


def _make_dummy_target(tmp_path: Path, filename: str = "module.py", content: str = "x = 1\n") -> TargetRecord:
    """Helper to create a temporary file and return a valid TargetRecord."""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return TargetRecord(
        path=filename,
        absolute_path=str(p.resolve()),
        file_size_bytes=len(p.read_bytes()),
        sha256="0" * 64,
        encoding="utf-8",
        has_bom=False,
        newline_style="\n",
        has_trailing_newline=True,
    )


# =============================================================================
# 1. Line Coordinate Mapping Unit Tests
# =============================================================================


class TestLineCoordinateMapping:
    """Tests for line shift formulas and coordinate mapping."""

    def test_empty_edits_returns_identity(self) -> None:
        assert compute_line_shift_before(10, []) == 0
        assert map_baseline_line_to_candidate(10, []) == 10
        assert is_candidate_line_modified(10, []) is False

    def test_single_insertion_shifts_subsequent_lines(self) -> None:
        # Insert 3 lines before baseline line 5
        edit = EditOperation(
            operation=EditOperationType.INSERT,
            start_line=5,
            end_line=4,
            expected_text="",
            replacement_text="line_a\nline_b\nline_c",
        )
        edits = [edit]

        # Lines before insertion are unchanged
        assert map_baseline_line_to_candidate(1, edits) == 1
        assert map_baseline_line_to_candidate(4, edits) == 4

        # Baseline line 5 and subsequent lines shift down by +3
        assert map_baseline_line_to_candidate(5, edits) == 8
        assert map_baseline_line_to_candidate(10, edits) == 13

        # Inserted candidate lines are lines 5, 6, 7
        assert is_candidate_line_modified(4, edits) is False
        assert is_candidate_line_modified(5, edits) is True
        assert is_candidate_line_modified(6, edits) is True
        assert is_candidate_line_modified(7, edits) is True
        assert is_candidate_line_modified(8, edits) is False

    def test_single_deletion_shifts_subsequent_lines_up(self) -> None:
        # Delete baseline lines 3 to 5 (3 lines)
        edit = EditOperation(
            operation=EditOperationType.DELETE,
            start_line=3,
            end_line=5,
            expected_text="d1\nd2\nd3",
            replacement_text="",
        )
        edits = [edit]

        # Lines before deletion unchanged
        assert map_baseline_line_to_candidate(1, edits) == 1
        assert map_baseline_line_to_candidate(2, edits) == 2

        # Lines inside deletion mapped to None
        assert map_baseline_line_to_candidate(3, edits) is None
        assert map_baseline_line_to_candidate(4, edits) is None
        assert map_baseline_line_to_candidate(5, edits) is None

        # Lines after deletion shift up by -3
        assert map_baseline_line_to_candidate(6, edits) == 3
        assert map_baseline_line_to_candidate(10, edits) == 7

        # Deletion does not introduce new modified candidate lines
        assert is_candidate_line_modified(1, edits) is False
        assert is_candidate_line_modified(2, edits) is False
        assert is_candidate_line_modified(3, edits) is False

    def test_replacement_with_expansion(self) -> None:
        # Replace line 4 (1 line) with 3 lines (net +2)
        edit = EditOperation(
            operation=EditOperationType.REPLACE,
            start_line=4,
            end_line=4,
            expected_text="old_line",
            replacement_text="new_1\nnew_2\nnew_3",
        )
        edits = [edit]

        assert map_baseline_line_to_candidate(3, edits) == 3
        assert map_baseline_line_to_candidate(4, edits) is None  # replaced
        assert map_baseline_line_to_candidate(5, edits) == 7  # 5 + 2

        # Candidate lines 4, 5, 6 are modified
        assert is_candidate_line_modified(3, edits) is False
        assert is_candidate_line_modified(4, edits) is True
        assert is_candidate_line_modified(5, edits) is True
        assert is_candidate_line_modified(6, edits) is True
        assert is_candidate_line_modified(7, edits) is False

    def test_multiple_non_overlapping_edits(self) -> None:
        # Edit 1: Replace line 2 with 2 lines (net +1)
        # Edit 2: Insert 2 lines before baseline line 6 (net +2)
        # Edit 3: Delete baseline line 10 (net -1)
        edits = [
            EditOperation(
                operation=EditOperationType.REPLACE,
                start_line=2,
                end_line=2,
                expected_text="line2",
                replacement_text="line2a\nline2b",
            ),
            EditOperation(
                operation=EditOperationType.INSERT,
                start_line=6,
                end_line=5,
                expected_text="",
                replacement_text="ins1\nins2",
            ),
            EditOperation(
                operation=EditOperationType.DELETE,
                start_line=10,
                end_line=10,
                expected_text="line10",
                replacement_text="",
            ),
        ]

        # Line 1: before all edits -> 1
        assert map_baseline_line_to_candidate(1, edits) == 1
        # Line 2: inside edit 1 -> None
        assert map_baseline_line_to_candidate(2, edits) is None
        # Line 3: after edit 1 (+1) -> 4
        assert map_baseline_line_to_candidate(3, edits) == 4
        # Line 5: after edit 1 (+1) -> 6
        assert map_baseline_line_to_candidate(5, edits) == 6
        # Line 6: after edit 1 (+1) and at edit 2 (+2) -> 6 + 1 + 2 = 9
        assert map_baseline_line_to_candidate(6, edits) == 9
        # Line 9: after edit 1 (+1) and edit 2 (+2) -> 9 + 3 = 12
        assert map_baseline_line_to_candidate(9, edits) == 12
        # Line 10: inside edit 3 -> None
        assert map_baseline_line_to_candidate(10, edits) is None
        # Line 11: after edit 1 (+1), edit 2 (+2), edit 3 (-1) -> net +2 -> 13
        assert map_baseline_line_to_candidate(11, edits) == 13

    def test_patch_candidate_helper_methods(self, tmp_path: Path) -> None:
        source = "a\nb\nc\nd\ne\n"
        proposal = EditProposalRecord(
            target_file="test.py",
            explanation="Insert x and y before line 3",
            edits=[
                EditOperation(
                    operation=EditOperationType.INSERT,
                    start_line=3,
                    end_line=2,
                    expected_text="",
                    replacement_text="x\ny",
                )
            ],
        )
        applier = PatchApplier()
        candidate = applier.apply(proposal, target=tmp_path / "test.py", source_text=source)

        assert candidate.map_baseline_line(1) == 1
        assert candidate.map_baseline_line(2) == 2
        assert candidate.map_baseline_line(3) == 5  # shifted by +2
        assert candidate.is_line_modified(3) is True
        assert candidate.is_line_modified(4) is True
        assert candidate.is_line_modified(5) is False


# =============================================================================
# 2. Diagnostic Diffing Unit Tests
# =============================================================================


class TestCompareDiagnostics:
    """Tests for compare_diagnostics differential matching."""

    def test_empty_diagnostics_produces_empty_diff(self) -> None:
        diff = compare_diagnostics(baseline_diagnostics=[], candidate_diagnostics=[])
        assert isinstance(diff, DiagnosticDiffResult)
        assert diff.pre_existing == []
        assert diff.eliminated == []
        assert diff.introduced == []
        assert diff.baseline_count == 0
        assert diff.candidate_count == 0

    def test_unchanged_diagnostics_categorized_as_pre_existing(self) -> None:
        d1 = _make_diagnostic(code="E501", line=10)
        d2 = _make_diagnostic(code="F401", line=20)
        diff = compare_diagnostics(
            baseline_diagnostics=[d1, d2],
            candidate_diagnostics=[d1, d2],
        )
        assert len(diff.pre_existing) == 2
        assert len(diff.eliminated) == 0
        assert len(diff.introduced) == 0

    def test_diagnostics_with_shifted_lines_matched_cleanly(self) -> None:
        # Baseline has diagnostic at line 10
        # Edit inserts 5 lines before line 2
        d_base = _make_diagnostic(code="E501", line=10)
        d_cand = _make_diagnostic(code="E501", line=15)

        edit = EditOperation(
            operation=EditOperationType.INSERT,
            start_line=2,
            end_line=1,
            expected_text="",
            replacement_text="1\n2\n3\n4\n5",
        )

        diff = compare_diagnostics(
            baseline_diagnostics=[d_base],
            candidate_diagnostics=[d_cand],
            edits=[edit],
        )

        assert len(diff.pre_existing) == 1
        assert diff.pre_existing[0].start_line == 15
        assert len(diff.eliminated) == 0
        assert len(diff.introduced) == 0
        assert diff.line_shifts[10] == 15

    def test_diagnostic_on_deleted_line_is_eliminated(self) -> None:
        d_base = _make_diagnostic(code="F401", line=5)
        edit = EditOperation(
            operation=EditOperationType.DELETE,
            start_line=5,
            end_line=5,
            expected_text="import unused",
            replacement_text="",
        )

        diff = compare_diagnostics(
            baseline_diagnostics=[d_base],
            candidate_diagnostics=[],
            edits=[edit],
        )

        assert len(diff.pre_existing) == 0
        assert len(diff.eliminated) == 1
        assert diff.eliminated[0].code == "F401"
        assert len(diff.introduced) == 0
        assert diff.line_shifts[5] is None

    def test_newly_introduced_diagnostics_in_candidate(self) -> None:
        d_base = _make_diagnostic(code="E501", line=10)
        d_new = _make_diagnostic(code="F841", line=3, message="Local variable 'x' unused")

        diff = compare_diagnostics(
            baseline_diagnostics=[d_base],
            candidate_diagnostics=[d_base, d_new],
        )

        assert len(diff.pre_existing) == 1
        assert len(diff.eliminated) == 0
        assert len(diff.introduced) == 1
        assert diff.introduced[0].code == "F841"

    def test_multiset_matching_same_code_same_line(self) -> None:
        # Baseline has 2 unused imports on line 1
        d1 = _make_diagnostic(code="F401", line=1, col=8, message="'os' imported but unused")
        d2 = _make_diagnostic(code="F401", line=1, col=12, message="'sys' imported but unused")

        # Candidate eliminated 'os' and kept 'sys' on line 1
        c1 = _make_diagnostic(code="F401", line=1, col=12, message="'sys' imported but unused")

        diff = compare_diagnostics(
            baseline_diagnostics=[d1, d2],
            candidate_diagnostics=[c1],
        )

        assert len(diff.pre_existing) == 1
        assert diff.pre_existing[0].message == "'sys' imported but unused"
        assert len(diff.eliminated) == 1
        assert diff.eliminated[0].message == "'os' imported but unused"
        assert len(diff.introduced) == 0


# =============================================================================
# 3. Validation Level A Certification Tests (Core Plan Test Cases)
# =============================================================================


class TestEvaluateLevelA:
    """Tests certifying Validation Level A (Static validity) per Phase 9 specification."""

    def test_candidate_resolving_static_finding_passes_level_a(self) -> None:
        """Case 1: Candidate resolves a static finding with unchanged baseline."""
        target_diag = _make_diagnostic(code="F401", line=1, message="'unused_pkg' imported but unused")
        candidate_code = "def add(a, b):\n    return a + b\n"

        edit = EditOperation(
            operation=EditOperationType.DELETE,
            start_line=1,
            end_line=1,
            expected_text="import unused_pkg",
            replacement_text="",
        )

        result: LevelAResult = evaluate_level_a(
            candidate_source=candidate_code,
            baseline_diagnostics=[target_diag],
            candidate_diagnostics=[],
            edits=[edit],
            targeted_diagnostics=[target_diag],
        )

        assert result.passed is True
        assert result.syntax_valid is True
        assert result.targeted_eliminated is True
        assert len(result.diagnostic_diff.eliminated) == 1
        assert len(result.diagnostic_diff.introduced) == 0
        assert result.failure_reasons == []

    def test_runtime_only_repair_on_clean_baseline_passes_level_a(self) -> None:
        """Case 2: Runtime-only repair where no static findings existed in baseline.

        Certifies Level A without requiring a non-existent static finding to eliminate.
        """
        candidate_code = """\
def divide(a, b):
    if b == 0:
        return 0
    return a // b
"""
        result: LevelAResult = evaluate_level_a(
            candidate_source=candidate_code,
            baseline_diagnostics=[],
            candidate_diagnostics=[],
            edits=None,
            targeted_diagnostics=None,
            baseline_syntax_valid=True,
        )

        assert result.passed is True
        assert result.syntax_valid is True
        assert result.targeted_eliminated is True
        assert len(result.diagnostic_diff.introduced) == 0
        assert len(result.diagnostic_diff.eliminated) == 0
        assert result.failure_reasons == []

    def test_candidate_with_shifted_line_numbers_tolerates_pre_existing(self) -> None:
        """Case 3: Unrelated pre-existing findings with shifted lines do not fail Level A."""
        pre_existing_diag = _make_diagnostic(code="E501", line=10, message="Line too long (95 > 88)")
        candidate_code = "def new_function():\n    pass\n\nprint('hello')\n"

        # Shift of +5 lines from an insertion
        edit = EditOperation(
            operation=EditOperationType.INSERT,
            start_line=2,
            end_line=1,
            expected_text="",
            replacement_text="a\nb\nc\nd\ne",
        )

        shifted_cand_diag = _make_diagnostic(code="E501", line=15, message="Line too long (95 > 88)")

        result: LevelAResult = evaluate_level_a(
            candidate_source=candidate_code,
            baseline_diagnostics=[pre_existing_diag],
            candidate_diagnostics=[shifted_cand_diag],
            edits=[edit],
            targeted_diagnostics=None,
        )

        assert result.passed is True
        assert result.syntax_valid is True
        assert len(result.diagnostic_diff.pre_existing) == 1
        assert len(result.diagnostic_diff.introduced) == 0
        assert result.failure_reasons == []

    def test_candidate_introducing_new_syntax_error_fails_level_a(self) -> None:
        """Case 4: Candidate introducing a syntax error must fail Level A."""
        broken_candidate_code = "def broken_syntax(:\n    pass\n"

        result: LevelAResult = evaluate_level_a(
            candidate_source=broken_candidate_code,
            baseline_diagnostics=[],
            candidate_diagnostics=None,  # let evaluate_level_a detect syntax error
        )

        assert result.passed is False
        assert result.syntax_valid is False
        assert len(result.syntax_diagnostics) == 1
        assert result.syntax_diagnostics[0].code == "SyntaxError"
        assert any("syntax" in r.lower() for r in result.failure_reasons)

    def test_candidate_introducing_new_ruff_warning_fails_level_a(self) -> None:
        """Case 5: Candidate introducing a new static finding must fail Level A."""
        candidate_code = "def func():\n    unused_var = 42\n    return 1\n"
        new_diag = _make_diagnostic(code="F841", line=2, message="Local variable 'unused_var' is assigned to but never used")

        result: LevelAResult = evaluate_level_a(
            candidate_source=candidate_code,
            baseline_diagnostics=[],
            candidate_diagnostics=[new_diag],
        )

        assert result.passed is False
        assert result.syntax_valid is True
        assert len(result.diagnostic_diff.introduced) == 1
        assert result.diagnostic_diff.introduced[0].code == "F841"
        assert any("F841" in r for r in result.failure_reasons)

    def test_targeted_diagnostic_not_eliminated_fails_level_a(self) -> None:
        """Candidate that fails to eliminate targeted diagnostic fails Level A."""
        target_diag = _make_diagnostic(code="F401", line=1, message="'sys' imported but unused")
        candidate_code = "import sys\n\ndef add(a, b):\n    return a + b\n"

        result: LevelAResult = evaluate_level_a(
            candidate_source=candidate_code,
            baseline_diagnostics=[target_diag],
            candidate_diagnostics=[target_diag],
            targeted_diagnostics=[target_diag],
        )

        assert result.passed is False
        assert result.targeted_eliminated is False
        assert any("F401" in r for r in result.failure_reasons)

    def test_targeted_code_string_eliminated(self) -> None:
        """Targeted diagnostic can be specified by rule code string (e.g. 'F401')."""
        target_diag = _make_diagnostic(code="F401", line=1)
        candidate_code = "def f(): return 1\n"

        edit = EditOperation(
            operation=EditOperationType.DELETE,
            start_line=1,
            end_line=1,
            expected_text="import os",
            replacement_text="",
        )

        result: LevelAResult = evaluate_level_a(
            candidate_source=candidate_code,
            baseline_diagnostics=[target_diag],
            candidate_diagnostics=[],
            edits=[edit],
            targeted_diagnostics=["F401"],
        )

        assert result.passed is True
        assert result.targeted_eliminated is True

    def test_baseline_syntax_error_eliminated_by_clean_candidate(self) -> None:
        """Baseline with syntax error repaired to valid code passes Level A."""
        candidate_code = "def valid():\n    return 42\n"

        result: LevelAResult = evaluate_level_a(
            candidate_source=candidate_code,
            baseline_diagnostics=[],
            candidate_diagnostics=[],
            targeted_diagnostics=["SyntaxError"],
            baseline_syntax_valid=False,
        )

        assert result.passed is True
        assert result.syntax_valid is True
        assert result.targeted_eliminated is True


# =============================================================================
# 4. Integration with Adapter and Evidence Helpers
# =============================================================================


class TestValidationLevelAIntegration:
    """Integration tests connecting Level A with PythonAdapter and evidence collection."""

    def test_python_adapter_validates_candidate_level_a_success(self, tmp_path: Path) -> None:
        target = _make_dummy_target(tmp_path, "clean_target.py", "x = 10\n")
        cand_file = tmp_path / "candidate.py"
        cand_file.write_text("x = 20\n", encoding="utf-8")

        adapter = PythonAdapter()
        val_report = adapter.validate_candidate(
            target=target,
            candidate_path=cand_file,
            baseline_diagnostics=[],
        )

        assert val_report.static_valid is True
        assert val_report.level_achieved in (ValidationLevel.LEVEL_A, ValidationLevel.LEVEL_B, ValidationLevel.LEVEL_C)
        assert "level_a" in val_report.details

    def test_python_adapter_validates_candidate_level_a_syntax_failure(self, tmp_path: Path) -> None:
        target = _make_dummy_target(tmp_path, "target.py", "x = 10\n")
        cand_file = tmp_path / "candidate_broken.py"
        cand_file.write_text("def broken(:\n", encoding="utf-8")

        adapter = PythonAdapter()
        val_report = adapter.validate_candidate(
            target=target,
            candidate_path=cand_file,
        )

        assert val_report.static_valid is False
        assert val_report.level_achieved == ValidationLevel.NONE
        assert "failure_reasons" in val_report.details

    def test_evaluate_candidate_level_a_evidence_helper(self, tmp_path: Path) -> None:
        target = _make_dummy_target(tmp_path, "target.py", "def add(): pass\n")
        baseline_diag = _make_diagnostic(code="F401", line=1)
        analysis_report = AnalysisReport(
            target=target,
            total_lines=1,
            syntax_valid=True,
            syntax_diagnostics=[],
            ast_facts=None,
            diagnostics=[baseline_diag],
        )

        candidate_code = "def add(): pass\n"
        result = evaluate_candidate_level_a(
            target=target,
            candidate_source=candidate_code,
            baseline_analysis=analysis_report,
            candidate_diagnostics=[],
            targeted_diagnostics=[baseline_diag],
        )

        assert result.passed is True
        assert result.targeted_eliminated is True
