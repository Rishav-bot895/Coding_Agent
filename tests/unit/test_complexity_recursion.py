"""Unit tests for bounded recursion analysis and abstention (Phase 10 Task P10-T3).

Verifies:
1. Direct linear recursion (factorial, linear sequence traversal):
   - O(n) time, O(n) auxiliary call-stack space.
2. Fixed divide-and-conquer (halving recursion, binary search interval halving):
   - O(log n) time, O(log n) auxiliary call-stack space.
3. Binary branching recursion without memoization (naive Fibonacci):
   - O(2^n) time, O(n) auxiliary call-stack space.
4. Call-stack depth accounting: recursion depth is explicitly included in auxiliary space.
5. Mandatory abstention policy:
   - Missing static base case: abstains with DYNAMIC_RECURSION.
   - Mutual recursion (module-level and class-level): abstains with DYNAMIC_RECURSION.
   - Dynamic or data-dependent recurrence steps: abstains with DYNAMIC_RECURSION.
   - Dynamic branching recursion (recursive calls in loops): abstains with DYNAMIC_RECURSION.
"""

from __future__ import annotations

from pathlib import Path

from localdev.languages.python.complexity import (
    CostModelAnalyzer,
    analyze_complexity,
)
from localdev.schemas import (
    ComplexityAbstentionReason,
    ComplexityClassEnum,
    ConfidenceEnum,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "complexity_samples" / "recursive"
PATTERNS_FILE = FIXTURES_DIR / "patterns.py"


# =============================================================================
# Direct Linear Recursion Tests
# =============================================================================


def test_factorial_linear_recursion() -> None:
    """Direct integer decrement recursion yields O(n) time and O(n) call-stack space."""
    report = analyze_complexity(PATTERNS_FILE, selector="factorial")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.confidence == ConfidenceEnum.HIGH
    assert report.abstention_reason is None
    assert any("call-stack" in a.lower() or "linear recursion" in a.lower() for a in report.assumptions)


def test_linear_traversal_slice_recursion() -> None:
    """Sequence slicing linear recursion yields O(n) time and O(n) aux space."""
    report = analyze_complexity(PATTERNS_FILE, selector="linear_traversal")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.confidence == ConfidenceEnum.HIGH
    assert report.abstention_reason is None


def test_ternary_if_expression_base_case() -> None:
    """Recursive function using ternary if-expression base case is recognized."""
    code = """
def fact_ternary(n: int) -> int:
    return 1 if n <= 1 else n * fact_ternary(n - 1)
"""
    analyzer = CostModelAnalyzer("ternary.py", code)
    report = analyzer.analyze_selector("fact_ternary")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.abstention_reason is None


def test_inverted_branch_base_case() -> None:
    """Recursive function with inverted condition and fallthrough base case is recognized."""
    code = """
def fact_inverted(n: int) -> int:
    if n > 1:
        return n * fact_inverted(n - 1)
    return 1
"""
    analyzer = CostModelAnalyzer("inverted.py", code)
    report = analyzer.analyze_selector("fact_inverted")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.abstention_reason is None


# =============================================================================
# Divide-and-Conquer Recursion Tests
# =============================================================================


def test_halving_divide_and_conquer() -> None:
    """Integer division by 2 halving recursion yields O(log n) time and O(log n) space."""
    report = analyze_complexity(PATTERNS_FILE, selector="halving_rec")
    assert report.time_complexity == ComplexityClassEnum.O_LOG_N
    assert report.auxiliary_space == ComplexityClassEnum.O_LOG_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.confidence == ConfidenceEnum.HIGH
    assert report.abstention_reason is None
    assert any("logarithmic" in a.lower() or "divide-and-conquer" in a.lower() for a in report.assumptions)


def test_binary_search_recursion() -> None:
    """Binary search interval halving yields O(log n) time and O(log n) aux space."""
    report = analyze_complexity(PATTERNS_FILE, selector="binary_search_rec")
    assert report.time_complexity == ComplexityClassEnum.O_LOG_N
    assert report.auxiliary_space == ComplexityClassEnum.O_LOG_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.confidence == ConfidenceEnum.HIGH
    assert report.abstention_reason is None


# =============================================================================
# Binary Branching Recursion Tests (O(2^n))
# =============================================================================


def test_fibonacci_naive_binary_branching() -> None:
    """Binary branching recursion without memoization yields O(2^n) time and O(n) space."""
    report = analyze_complexity(PATTERNS_FILE, selector="fibonacci_naive")
    assert report.time_complexity == ComplexityClassEnum.O_2N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.confidence == ConfidenceEnum.HIGH
    assert report.abstention_reason is None
    assert any("o(2^n)" in a.lower() or "binary branching" in a.lower() for a in report.assumptions)


# =============================================================================
# Recursive Class Methods Tests
# =============================================================================


def test_class_method_linear_recursion() -> None:
    """Recursive method on self is correctly recognized."""
    code = """
class RecursiveCalculator:
    def countdown(self, n: int) -> int:
        if n <= 0:
            return 0
        return self.countdown(n - 1)
"""
    analyzer = CostModelAnalyzer("class_rec.py", code)
    report = analyzer.analyze_selector("RecursiveCalculator.countdown")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.abstention_reason is None


def test_class_method_fibonacci_branching() -> None:
    """Binary branching recursive method on self yields O(2^n) time and O(n) space."""
    code = """
class RecursiveCalculator:
    def fib(self, n: int) -> int:
        if n <= 1:
            return n
        return self.fib(n - 1) + self.fib(n - 2)
"""
    analyzer = CostModelAnalyzer("class_fib.py", code)
    report = analyzer.analyze_selector("RecursiveCalculator.fib")
    assert report.time_complexity == ComplexityClassEnum.O_2N
    assert report.auxiliary_space == ComplexityClassEnum.O_N
    assert report.abstention_reason is None


# =============================================================================
# Mandatory Abstention Policy Tests
# =============================================================================


def test_missing_base_case_abstention() -> None:
    """Recursion without recognizable static base case abstains with DYNAMIC_RECURSION."""
    report = analyze_complexity(PATTERNS_FILE, selector="missing_base_case")
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.auxiliary_space == ComplexityClassEnum.UNKNOWN
    assert report.abstention_reason == ComplexityAbstentionReason.DYNAMIC_RECURSION
    assert "base case" in report.details.lower()


def test_both_branches_recursive_abstention() -> None:
    """Recursion where all branches are recursive lacks static base case and abstains."""
    code = """
def infinite_branching(n: int) -> int:
    if n > 0:
        return infinite_branching(n - 1)
    else:
        return infinite_branching(n + 1)
"""
    analyzer = CostModelAnalyzer("infinite.py", code)
    report = analyzer.analyze_selector("infinite_branching")
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.abstention_reason == ComplexityAbstentionReason.DYNAMIC_RECURSION


def test_mutual_recursion_abstention() -> None:
    """Mutual recursion between functions abstains with DYNAMIC_RECURSION."""
    report_even = analyze_complexity(PATTERNS_FILE, selector="mutual_even")
    assert report_even.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report_even.abstention_reason == ComplexityAbstentionReason.DYNAMIC_RECURSION
    assert "mutual recursion" in report_even.details.lower()

    report_odd = analyze_complexity(PATTERNS_FILE, selector="mutual_odd")
    assert report_odd.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report_odd.abstention_reason == ComplexityAbstentionReason.DYNAMIC_RECURSION
    assert "mutual recursion" in report_odd.details.lower()


def test_class_method_mutual_recursion_abstention() -> None:
    """Mutual recursion between class methods abstains with DYNAMIC_RECURSION."""
    code = """
class MutualService:
    def ping(self, n: int) -> int:
        if n <= 0:
            return 0
        return self.pong(n - 1)

    def pong(self, n: int) -> int:
        if n <= 0:
            return 0
        return self.ping(n - 1)
"""
    analyzer = CostModelAnalyzer("mutual_cls.py", code)
    report = analyzer.analyze_selector("MutualService.ping")
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.abstention_reason == ComplexityAbstentionReason.DYNAMIC_RECURSION
    assert "mutual recursion" in report.details.lower()


def test_dynamic_collatz_step_abstention() -> None:
    """Data-dependent recurrence steps abstain with DYNAMIC_RECURSION."""
    report = analyze_complexity(PATTERNS_FILE, selector="dynamic_collatz")
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.auxiliary_space == ComplexityClassEnum.UNKNOWN
    assert report.abstention_reason == ComplexityAbstentionReason.DYNAMIC_RECURSION
    assert "dynamic" in report.details.lower() or "data-dependent" in report.details.lower()


def test_dynamic_branching_loop_recursion_abstention() -> None:
    """Recursive call inside a loop body abstains with DYNAMIC_RECURSION."""
    report = analyze_complexity(PATTERNS_FILE, selector="loop_recursion")
    assert report.time_complexity == ComplexityClassEnum.UNKNOWN
    assert report.auxiliary_space == ComplexityClassEnum.UNKNOWN
    assert report.abstention_reason == ComplexityAbstentionReason.DYNAMIC_RECURSION
    assert "loop" in report.details.lower()
