"""Unit tests for human-readable terminal reporting and snapshot formatting (P2-T4).

Verifies structured rendering of banners, evidence lists, unified diffs, validation
levels (Levels A–D), complexity bounds, profiling metrics, technical limitations,
errors, and snapshot assertions for success, failure, timeout, and abstention reports.
Verifies active terminal sanitization and redirection to non-TTY streams.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from localdev.reporting.terminal import TerminalReporter
from localdev.schemas import ValidationLevel


# =============================================================================
# Section Formatting & Sanitization Tests
# =============================================================================


def test_reporter_write_applies_sanitization() -> None:
    """TerminalReporter.write() strips hostile ANSI sequences and control characters."""
    stream = io.StringIO()
    reporter = TerminalReporter(stream=stream, enable_sanitization=True)

    hostile_input = "Executing\x00 \x1b[31;1mDanger\x1b[0m \x1b[2JScreenClear\n"
    reporter.write(hostile_input)

    output = stream.getvalue()
    assert "\x1b[" not in output
    assert "\x00" not in output
    assert "Executing Danger ScreenClear\n" == output


def test_render_header() -> None:
    """Header renders formatted banner with command name and status tag."""
    reporter = TerminalReporter()
    h_success = reporter.render_header("analyse", target="script.py", success=True)
    assert "=== localdev ANALYSE [SUCCESS] ===" in h_success
    assert "Target: script.py" in h_success

    h_failed = reporter.render_header("debug", target="script.py", success=False)
    assert "=== localdev DEBUG [FAILED] ===" in h_failed


def test_render_evidence_list() -> None:
    """Evidence list formats bulleted items."""
    reporter = TerminalReporter()
    rendered = reporter.render_evidence_list(
        "Static Findings",
        ["Line 12: ZeroDivisionError risk", "Line 45: Unused import"],
    )
    assert "--- Static Findings ---" in rendered
    assert "  • Line 12: ZeroDivisionError risk" in rendered
    assert "  • Line 45: Unused import" in rendered


def test_render_diff() -> None:
    """Unified diff block renders under designated section title."""
    reporter = TerminalReporter()
    diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x = 0\n+x = 1"
    rendered = reporter.render_diff(diff)
    assert "--- Proposed Unified Diff ---" in rendered
    assert "+x = 1" in rendered


def test_render_validation_levels() -> None:
    """Validation levels display Levels A–D status and Level B non-proof disclaimer."""
    reporter = TerminalReporter()
    checks = {
        "static_valid": True,
        "failure_reproduction_removed": True,
        "clean_execution": False,
        "behavioral_oracle_passed": None,
    }
    rendered = reporter.render_validation_levels(ValidationLevel.LEVEL_B, checks)

    assert "Highest Level Achieved: B" in rendered
    assert "[OK] Level A" in rendered
    assert "[OK] Level B" in rendered
    assert "[FAIL] Level C" in rendered
    assert "[-] Level D" in rendered
    assert "does NOT prove correctness" in rendered


def test_render_complexity_report() -> None:
    """Complexity report renders time, auxiliary space, output space, and assumptions."""
    reporter = TerminalReporter()
    rendered = reporter.render_complexity_report(
        target="module.py::sort_items",
        time_comp="O(n log n)",
        aux_space="O(n)",
        output_space="O(n)",
        confidence="HIGH",
        assumptions=["List elements implement total ordering", "CPython Timsort semantics"],
        details="Grounded in iterative CPython Timsort mechanics.",
    )
    assert "Complexity Analysis: module.py::sort_items" in rendered
    assert "Time Complexity:       O(n log n)" in rendered
    assert "Auxiliary Space:       O(n)" in rendered
    assert "Confidence:            HIGH" in rendered
    assert "CPython Timsort semantics" in rendered


def test_render_profile_report() -> None:
    """Profile report renders separated import, latency, tracemalloc heap, and RSS metrics."""
    reporter = TerminalReporter()
    rendered = reporter.render_profile_report(
        target="script.py::process_data",
        import_ms=12.45,
        latency_median_ms=1.234,
        latency_dispersion_ms=0.045,
        tracemalloc_bytes=24576,  # 24 KB
        rss_bytes=15728640,  # 15 MB
        warmup_runs=2,
        measured_runs=7,
        hot_process=True,
    )
    assert "Function Profile: script.py::process_data" in rendered
    assert "Import Cost:           12.45 ms" in rendered
    assert "Median Latency:        1.234 ms (dispersion: ±0.045 ms)" in rendered
    assert "Python Heap (Peak):    24.0 KB" in rendered
    assert "Worker Process RSS:    15.00 MB" in rendered
    assert "Hot process (module state persistent)" in rendered


# =============================================================================
# Snapshot Tests
# =============================================================================


def test_snapshot_successful_report() -> None:
    """Snapshot test for a completely successful repair workflow."""
    reporter = TerminalReporter()
    report = reporter.render_report(
        command="fix",
        success=True,
        target_path="target.py",
        summary="Bug diagnosed and successfully repaired via local SLM proposal.",
        evidence=["ZeroDivisionError: division by zero at line 5"],
        diff="@@ -5 +5 @@\n-return a / b\n+return a / b if b != 0 else 0",
        validation=(
            "D",
            {
                "static_valid": True,
                "failure_reproduction_removed": True,
                "clean_execution": True,
                "behavioral_oracle_passed": True,
            },
        ),
    )

    assert "=== localdev FIX [SUCCESS] ===" in report
    assert "Target: target.py" in report
    assert "Bug diagnosed and successfully repaired" in report
    assert "Proposed Unified Diff" in report
    assert "Highest Level Achieved: D" in report
    assert "[OK] Level D" in report


def test_snapshot_failure_report() -> None:
    """Snapshot test for a failed execution / bug diagnosed report."""
    reporter = TerminalReporter()
    report = reporter.render_report(
        command="debug",
        success=False,
        target_path="buggy.py",
        summary="Target script execution failed under controlled runtime (-E -B -P).",
        evidence=["IndexError: list index out of range at line 14"],
        errors=["Process exited with return code 1"],
    )

    assert "=== localdev DEBUG [FAILED] ===" in report
    assert "IndexError: list index out of range" in report
    assert "[ERROR] Process exited with return code 1" in report


def test_snapshot_timeout_report() -> None:
    """Snapshot test for a resource breach / timeout report."""
    reporter = TerminalReporter()
    report = reporter.render_report(
        command="debug",
        success=False,
        target_path="infinite_loop.py",
        summary="Subprocess execution terminated due to resource breach.",
        errors=["Wall-clock timeout of 10.0 seconds exceeded. Process tree terminated."],
        limitations=["Execution aborted before completion; output may be partial."],
    )

    assert "=== localdev DEBUG [FAILED] ===" in report
    assert "timeout of 10.0 seconds exceeded" in report
    assert "[!] Execution aborted before completion" in report


def test_snapshot_abstention_report() -> None:
    """Snapshot test for a safe complexity abstention report."""
    reporter = TerminalReporter()
    report = reporter.render_report(
        command="complexity",
        success=True,
        target_path="dynamic.py::solve",
        summary="Static complexity analysis safely abstained.",
        limitations=[
            "Abstained: DYNAMIC_BOUNDS - Loop bounds depend on dynamic runtime input.",
        ],
    )

    assert "=== localdev COMPLEXITY [SUCCESS] ===" in report
    assert "safely abstained" in report
    assert "DYNAMIC_BOUNDS" in report


# =============================================================================
# Stream Redirection Tests
# =============================================================================


def test_redirection_to_file(tmp_path: Path) -> None:
    """TerminalReporter writes cleanly to file streams (non-TTY)."""
    out_file = tmp_path / "output.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        reporter = TerminalReporter(stream=f)
        reporter.print_line("=== localdev INFO ===")
        reporter.print_line("File: sample.py")

    content = out_file.read_text(encoding="utf-8")
    assert "=== localdev INFO ===" in content
    assert "File: sample.py" in content

