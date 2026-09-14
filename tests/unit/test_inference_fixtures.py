"""Unit tests for initial bug samples and mock model response fixtures.

Verifies:
1. All 10 initial bug samples are valid Python syntax.
2. Each bug sample reproduces its expected runtime exception when executed.
3. Model response fixtures validate cleanly against DiagnosisRecord and EditProposalRecord.
4. Malformed fixtures trigger ValidationError for retry/rejection testing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from localdev.schemas import DiagnosisRecord, EditProposalRecord

BUG_SAMPLES_DIR = Path(__file__).parent.parent / "bug_samples" / "initial"
MODEL_RESPONSES_DIR = Path(__file__).parent.parent / "fixtures" / "model_responses"

EXPECTED_EXCEPTIONS = {
    "01_off_by_one.py": "IndexError",
    "02_zero_division.py": "ZeroDivisionError",
    "03_type_mismatch.py": "TypeError",
    "04_key_error.py": "KeyError",
    "05_attribute_error.py": "AttributeError",
    "06_unbound_local.py": "UnboundLocalError",
    "07_recursion_error.py": "RecursionError",
    "08_empty_sequence.py": "IndexError",
    "09_name_error.py": "NameError",
    "10_value_error.py": "ValueError",
}


def test_bug_samples_syntax_validity() -> None:
    """Verify that all 10 bug samples are syntactically valid Python code."""
    assert BUG_SAMPLES_DIR.exists(), f"Bug samples directory missing: {BUG_SAMPLES_DIR}"
    samples = sorted(BUG_SAMPLES_DIR.glob("*.py"))
    assert len(samples) == 10, f"Expected 10 bug samples, found {len(samples)}"

    for sample in samples:
        code = sample.read_text(encoding="utf-8")
        # compile() without executing proves valid AST/syntax
        compile(code, str(sample), mode="exec")


@pytest.mark.parametrize("filename,expected_exc", EXPECTED_EXCEPTIONS.items())
def test_bug_samples_runtime_reproduction(filename: str, expected_exc: str) -> None:
    """Verify each bug sample reproduces its expected exception when executed."""
    sample_path = BUG_SAMPLES_DIR / filename
    assert sample_path.exists(), f"Sample file {filename} does not exist"

    result = subprocess.run(
        [sys.executable, "-E", "-B", str(sample_path)],
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
    )
    assert result.returncode != 0, f"{filename} unexpectedly succeeded with exit code 0"
    assert expected_exc in result.stderr, (
        f"{filename} failed with exit code {result.returncode} but did not mention {expected_exc}. "
        f"Stderr: {result.stderr}"
    )


def test_diagnosis_model_response_fixtures() -> None:
    """Verify diagnosis model response fixtures parse into DiagnosisRecord."""
    diag_files = [
        "diagnosis_01_off_by_one.json",
        "diagnosis_02_zero_division.json",
        "diagnosis_03_type_mismatch.json",
    ]
    for filename in diag_files:
        path = MODEL_RESPONSES_DIR / filename
        assert path.exists(), f"Fixture missing: {filename}"
        record = DiagnosisRecord.model_validate_json(path.read_text(encoding="utf-8"))
        assert record.bug_description
        assert record.root_cause
        assert len(record.cited_evidence_ids) > 0


def test_edit_proposal_model_response_fixtures() -> None:
    """Verify edit proposal model response fixtures parse into EditProposalRecord."""
    edit_files = [
        "edit_proposal_01_off_by_one.json",
        "edit_proposal_02_zero_division.json",
        "edit_proposal_03_type_mismatch.json",
    ]
    for filename in edit_files:
        path = MODEL_RESPONSES_DIR / filename
        assert path.exists(), f"Fixture missing: {filename}"
        proposal = EditProposalRecord.model_validate_json(path.read_text(encoding="utf-8"))
        assert proposal.target_file
        assert len(proposal.edits) > 0
        assert proposal.explanation


def test_malformed_model_responses_rejected() -> None:
    """Verify structurally malformed model responses fail Pydantic validation."""
    malformed_diag = MODEL_RESPONSES_DIR / "diagnosis_malformed_shape.json"
    with pytest.raises(ValidationError):
        DiagnosisRecord.model_validate_json(malformed_diag.read_text(encoding="utf-8"))

    invalid_edit = MODEL_RESPONSES_DIR / "edit_proposal_invalid_lines.json"
    with pytest.raises(ValidationError):
        EditProposalRecord.model_validate_json(invalid_edit.read_text(encoding="utf-8"))

