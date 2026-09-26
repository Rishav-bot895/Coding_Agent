"""Unit tests for iterative algorithm complexity analysis (Phase 10 Task P10-T2).

Verifies:
1. Sequential loop addition (O(n) + O(n) = O(n)).
2. Nested loop multiplication:
   - Same dimension: O(n) * O(n) = O(n²)
   - Distinct dimensions: O(n) * O(m) = O(nm)
   - Triple nested: O(n) * O(n) * O(n) = O(n³)
   - 4-nested loops exceed vocabulary -> UNKNOWN with DYNAMIC_BOUNDS
3. Standard built-ins: len() O(1), range() O(1), sorted() O(n log n), append() amortized O(1).
4. Membership testing:
   - x in set / x in dict: O(1) expected time, O(n) total in loop
   - x in list: O(n) worst-case scan, O(n²) or O(nm) total in loop
5. Space separation:
   - Temporary list comprehension: aux O(n), output O(1)
   - Generator expression: aux O(1), output O(1)
   - Returned list comprehension: aux O(1), output O(n)
   - Slicing: creates copy (O(n) time & aux space, or output space if returned)
"""

from __future__ import annotations

from pathlib import Path

from localdev.languages.python.complexity import (
    analyze_complexity,
    combine_addition,
    combine_multiplication,
)
from localdev.schemas import (
    ComplexityAbstentionReason,
    ComplexityClassEnum,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "complexity_samples" / "iterative"
LOOPS_FILE = FIXTURES_DIR / "loops.py"
SPACE_FILE = FIXTURES_DIR / "space_allocation.py"
MEMBERSHIP_FILE = FIXTURES_DIR / "membership.py"


# =============================================================================
# Asymptotic Combining Logic Tests
# =============================================================================


def test_distinct_dimension_multiplication() -> None:
    """Distinct dimension multiplication produces O(nm)."""
    assert (
        combine_multiplication(
            ComplexityClassEnum.O_N,
            ComplexityClassEnum.O_N,
            distinct_dimensions=True,
        )
        == ComplexityClassEnum.O_NM
    )
    assert (
        combine_multiplication(
            ComplexityClassEnum.O_N,
            ComplexityClassEnum.O_N,
            distinct_dimensions=False,
        )
        == ComplexityClassEnum.O_N2
    )


def test_sequential_loop_addition_algebra() -> None:
    """Sequential loops sum to dominant term."""
    assert (
        combine_addition(ComplexityClassEnum.O_N, ComplexityClassEnum.O_N)
        == ComplexityClassEnum.O_N
    )
    assert (
        combine_addition(ComplexityClassEnum.O_1, ComplexityClassEnum.O_N)
        == ComplexityClassEnum.O_N
    )
    assert (
        combine_addition(ComplexityClassEnum.O_N, ComplexityClassEnum.O_N2)
        == ComplexityClassEnum.O_N2
    )


# =============================================================================
# Golden Fixtures: Supported Iterative Classes
# =============================================================================


def test_fixture_constant_operations() -> None:
    """Constant operations: O(1) time, O(1) aux, O(1) output."""
    report = analyze_complexity(LOOPS_FILE, selector="constant_operations")
    assert report.time_complexity == ComplexityClassEnum.O_1
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.is_amortized is False
    assert report.is_expected is False
    assert report.abstention_reason is None


def test_fixture_constant_range_loop() -> None:
    """Constant bounds loop: O(1) time, O(1) aux, O(1) output."""
    report = analyze_complexity(LOOPS_FILE, selector="constant_range_loop")
    assert report.time_complexity == ComplexityClassEnum.O_1
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("constant range loop" in a.lower() for a in report.assumptions)


def test_fixture_single_linear_loop() -> None:
    """Linear loop: O(n) time, O(1) aux, O(1) output."""
    report = analyze_complexity(LOOPS_FILE, selector="single_linear_loop")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("line" in a.lower() for a in report.assumptions)


def test_fixture_sequential_loops() -> None:
    """Sequential loops: O(n) + O(n) = O(n) time."""
    report = analyze_complexity(LOOPS_FILE, selector="sequential_loops")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("sequential loop addition" in a.lower() for a in report.assumptions)


def test_fixture_append_loop() -> None:
    """Loop appending to list: O(n) time, amortized O(1), O(n) output space."""
    report = analyze_complexity(LOOPS_FILE, selector="append_loop")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_N
    assert report.is_amortized is True
    assert any("dynamic array" in a.lower() for a in report.assumptions)


def test_fixture_builtin_sorted() -> None:
    """sorted(items): O(n log n) time, O(n) aux, O(n) output space."""
    report = analyze_complexity(LOOPS_FILE, selector="builtin_sorted")
    assert report.time_complexity == ComplexityClassEnum.O_N_LOG_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_N


def test_fixture_in_place_sort() -> None:
    """items.sort(): O(n log n) time, O(n) aux, O(1) output space."""
    report = analyze_complexity(LOOPS_FILE, selector="in_place_sort")
    assert report.time_complexity == ComplexityClassEnum.O_N_LOG_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1


def test_fixture_nested_same_dimension() -> None:
    """Pairwise nested loops on same dimension: O(n) * O(n) = O(n²)."""
    report = analyze_complexity(LOOPS_FILE, selector="nested_same_dimension")
    assert report.time_complexity == ComplexityClassEnum.O_N2
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("o(n²)" in a.lower() for a in report.assumptions)


def test_fixture_nested_distinct_dimensions() -> None:
    """Nested loops on distinct dimensions: O(n) * O(m) = O(nm)."""
    report = analyze_complexity(LOOPS_FILE, selector="nested_distinct_dimensions")
    assert report.time_complexity == ComplexityClassEnum.O_NM
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("o(nm)" in a.lower() for a in report.assumptions)


def test_fixture_triple_nested_loop() -> None:
    """Triple nested loop: O(n) * O(n) * O(n) = O(n³)."""
    report = analyze_complexity(LOOPS_FILE, selector="triple_nested_loop")
    assert report.time_complexity == ComplexityClassEnum.O_N3
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("o(n³)" in a.lower() for a in report.assumptions)


def test_fixture_four_nested_loops_abstains() -> None:
    """Four nested loops exceed closed vocabulary maximum O(n³) -> UNKNOWN."""
    report = analyze_complexity(LOOPS_FILE, selector="four_nested_loops")
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.abstention_reason == ComplexityAbstentionReason.DYNAMIC_BOUNDS
    assert "exceed" in report.details.lower()


# =============================================================================
# Golden Fixtures: Space Separation
# =============================================================================


def test_space_returned_list_comprehension() -> None:
    """Returned list comprehension: aux O(1), output O(n), time O(n)."""
    report = analyze_complexity(SPACE_FILE, selector="returned_list_comprehension")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_N
    assert any("materializes n elements" in a.lower() for a in report.assumptions)


def test_space_temporary_list_comprehension_consumed() -> None:
    """Temporary list comprehension consumed internally: aux O(n), output O(1)."""
    report = analyze_complexity(SPACE_FILE, selector="temporary_list_comprehension_consumed")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("auxiliary heap space" in a.lower() for a in report.assumptions)


def test_space_generator_expression_consumed() -> None:
    """Generator expression consumed in loop: aux O(1), output O(1)."""
    report = analyze_complexity(SPACE_FILE, selector="generator_expression_consumed")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("lazy evaluation" in a.lower() for a in report.assumptions)


def test_space_returned_generator_expression() -> None:
    """Returned generator expression: aux O(1), output O(1)."""
    report = analyze_complexity(SPACE_FILE, selector="returned_generator_expression")
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1


def test_space_generator_function_yield() -> None:
    """Generator function with yield: aux O(1), output O(1)."""
    report = analyze_complexity(SPACE_FILE, selector="generator_function_yield")
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("lazy iterator" in a.lower() for a in report.assumptions)


def test_space_temporary_slice() -> None:
    """Intermediate slicing: aux O(n), output O(1), time O(n)."""
    report = analyze_complexity(SPACE_FILE, selector="temporary_slice")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert any("intermediate slicing" in a.lower() for a in report.assumptions)


def test_space_returned_slice() -> None:
    """Returned slicing: aux O(1), output O(n), time O(n)."""
    report = analyze_complexity(SPACE_FILE, selector="returned_slice")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_N
    assert any("returned slice" in a.lower() for a in report.assumptions)


# =============================================================================
# Golden Fixtures: Membership Testing
# =============================================================================


def test_membership_in_set() -> None:
    """Set membership test is expected O(1), yielding O(n) total in loop."""
    report = analyze_complexity(MEMBERSHIP_FILE, selector="membership_in_set")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.is_expected is True
    assert any("uniform hash" in a.lower() for a in report.assumptions)


def test_membership_in_local_set() -> None:
    """Local set accumulator: expected O(1) membership, O(n) aux space."""
    report = analyze_complexity(MEMBERSHIP_FILE, selector="membership_in_local_set")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.is_expected is True
    assert any("internal container" in a.lower() for a in report.assumptions)


def test_membership_in_list() -> None:
    """List membership test is O(n) sequence scan, yielding O(nm) or O(n²) in loop."""
    report = analyze_complexity(MEMBERSHIP_FILE, selector="membership_in_list")
    assert report.time_complexity in (ComplexityClassEnum.O_NM, ComplexityClassEnum.O_N2)
    assert report.is_expected is False
    assert any("scans sequence" in a.lower() for a in report.assumptions)


def test_membership_in_local_list() -> None:
    """Local list membership test scans sequence, yielding O(n²) time and O(n) aux space."""
    report = analyze_complexity(MEMBERSHIP_FILE, selector="membership_in_local_list")
    assert report.time_complexity == ComplexityClassEnum.O_N2
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.is_expected is False
    assert any("linear membership scan" in a.lower() for a in report.assumptions)


def test_membership_in_dict() -> None:
    """Dict key membership test is expected O(1), yielding O(n) total in loop."""
    report = analyze_complexity(MEMBERSHIP_FILE, selector="membership_in_dict")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.is_expected is True
    assert any("uniform hash" in a.lower() for a in report.assumptions)
