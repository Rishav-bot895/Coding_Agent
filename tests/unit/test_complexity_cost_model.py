"""Unit tests for the restricted static complexity cost model (Phase 10 Task P10-T1).

Verifies:
1. Closed asymptotic vocabulary: strictly ComplexityClassEnum members.
2. Dominance ordering and asymptotic term addition (combine_addition).
3. Loop nesting multiplication (combine_multiplication).
4. Abstention report creation and reason codes.
5. Syntax error handling and unparseable input abstention.
"""

from __future__ import annotations

from localdev.languages.python.complexity import (
    analyze_complexity,
    combine_addition,
    combine_multiplication,
    create_abstention_report,
)
from localdev.schemas import (
    ComplexityAbstentionReason,
    ComplexityClassEnum,
    ConfidenceEnum,
)


def test_closed_vocabulary_members() -> None:
    """Verify closed asymptotic vocabulary contains exactly the 9 approved classes."""
    expected = {
        "O(1)",
        "O(log n)",
        "O(n)",
        "O(n log n)",
        "O(n²)",
        "O(n³)",
        "O(nm)",
        "O(2^n)",
        "UNKNOWN",
    }
    actual = {c.value for c in ComplexityClassEnum}
    assert actual == expected


def test_combine_addition_identity_and_dominance() -> None:
    """Verify asymptotic addition obeys Big-O dominance hierarchy."""
    assert combine_addition(ComplexityClassEnum.O_1, ComplexityClassEnum.O_1) == ComplexityClassEnum.O_1
    assert combine_addition(ComplexityClassEnum.O_1, ComplexityClassEnum.O_N) == ComplexityClassEnum.O_N
    assert combine_addition(ComplexityClassEnum.O_N, ComplexityClassEnum.O_1) == ComplexityClassEnum.O_N
    assert combine_addition(ComplexityClassEnum.O_N, ComplexityClassEnum.O_LOG_N) == ComplexityClassEnum.O_N
    assert combine_addition(ComplexityClassEnum.O_N, ComplexityClassEnum.O_N_LOG_N) == ComplexityClassEnum.O_N_LOG_N
    assert combine_addition(ComplexityClassEnum.O_N_LOG_N, ComplexityClassEnum.O_N2) == ComplexityClassEnum.O_N2
    assert combine_addition(ComplexityClassEnum.O_N2, ComplexityClassEnum.O_N3) == ComplexityClassEnum.O_N3
    assert combine_addition(ComplexityClassEnum.O_N3, ComplexityClassEnum.O_2N) == ComplexityClassEnum.O_2N


def test_combine_addition_with_unknown() -> None:
    """Verify addition with UNKNOWN conservatively yields UNKNOWN."""
    assert combine_addition(ComplexityClassEnum.UNKNOWN, ComplexityClassEnum.O_N) == ComplexityClassEnum.UNKNOWN
    assert combine_addition(ComplexityClassEnum.O_N, ComplexityClassEnum.UNKNOWN) == ComplexityClassEnum.UNKNOWN
    assert combine_addition(ComplexityClassEnum.UNKNOWN, ComplexityClassEnum.UNKNOWN) == ComplexityClassEnum.UNKNOWN


def test_combine_addition_with_bilinear() -> None:
    """Verify addition with bilinear O(nm)."""
    assert combine_addition(ComplexityClassEnum.O_NM, ComplexityClassEnum.O_1) == ComplexityClassEnum.O_NM
    assert combine_addition(ComplexityClassEnum.O_NM, ComplexityClassEnum.O_N) == ComplexityClassEnum.O_NM
    assert combine_addition(ComplexityClassEnum.O_NM, ComplexityClassEnum.O_N2) == ComplexityClassEnum.O_N2
    assert combine_addition(ComplexityClassEnum.O_NM, ComplexityClassEnum.O_N3) == ComplexityClassEnum.O_N3


def test_combine_multiplication() -> None:
    """Verify asymptotic loop multiplication."""
    # Identity with O(1)
    assert combine_multiplication(ComplexityClassEnum.O_1, ComplexityClassEnum.O_N) == ComplexityClassEnum.O_N
    assert combine_multiplication(ComplexityClassEnum.O_N, ComplexityClassEnum.O_1) == ComplexityClassEnum.O_N

    # Same dimension
    assert combine_multiplication(ComplexityClassEnum.O_N, ComplexityClassEnum.O_N) == ComplexityClassEnum.O_N2
    assert combine_multiplication(ComplexityClassEnum.O_N, ComplexityClassEnum.O_N2) == ComplexityClassEnum.O_N3

    # Distinct dimensions
    assert (
        combine_multiplication(ComplexityClassEnum.O_N, ComplexityClassEnum.O_N, distinct_dimensions=True)
        == ComplexityClassEnum.O_NM
    )

    # With log n
    assert combine_multiplication(ComplexityClassEnum.O_N, ComplexityClassEnum.O_LOG_N) == ComplexityClassEnum.O_N_LOG_N

    # Unknown propagation or out-of-bounds
    assert combine_multiplication(ComplexityClassEnum.UNKNOWN, ComplexityClassEnum.O_N) == ComplexityClassEnum.UNKNOWN
    assert combine_multiplication(ComplexityClassEnum.O_N2, ComplexityClassEnum.O_N2) == ComplexityClassEnum.UNKNOWN


def test_create_abstention_report() -> None:
    """Verify schema conformance of abstention reports."""
    report = create_abstention_report(
        target="example.py::func",
        reason=ComplexityAbstentionReason.UNKNOWN_CALL,
        details="Function calls unknown helper.",
        start_line=10,
        end_line=20,
    )
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.auxiliary_space == ComplexityClassEnum.UNKNOWN
    assert report.output_space == ComplexityClassEnum.UNKNOWN
    assert report.confidence == ConfidenceEnum.LOW
    assert report.abstention_reason == ComplexityAbstentionReason.UNKNOWN_CALL
    assert report.start_line == 10
    assert report.end_line == 20
    assert "unknown helper" in report.details


def test_syntax_error_abstention() -> None:
    """Verify syntax error in target file cleanly produces UNSUPPORTED_SYNTAX abstention."""
    broken_code = "def broken(:\n    pass\n"
    report = analyze_complexity("broken.py", source_text=broken_code)
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.abstention_reason == ComplexityAbstentionReason.UNSUPPORTED_SYNTAX
    assert "syntax" in report.details.lower()

