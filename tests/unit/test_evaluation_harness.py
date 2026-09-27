"""Unit tests for the evaluation dataset manifests and evaluation harness (P12-T1).

Verifies:
1. All four dataset manifests exist and conform to schema.
2. Sample counts meet specification requirements:
   - Bug dataset: 60-80 diverse samples (contains syntax, exceptions, logic, clean, external deps).
   - Complexity dataset: 35-50 functions (covers all 7 classes, aux vs output space, dynamic abstentions).
   - Profiling dataset: pure, mutating, stateful, memory-intensive, failing with validated JSON inputs.
   - Boundary dataset: filenames with spaces/Unicode, encodings, line endings, read-only, processes.
3. Every target file and input file referenced in manifests exists on disk.
4. Evaluation harness execution functions work offline and produce valid schema reports with 0 leaks.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.evaluate import (
    evaluate_boundary_dataset,
    evaluate_bug_dataset,
    evaluate_complexity_dataset,
    evaluate_profiling_dataset,
    run_full_evaluation,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


# =============================================================================
# Manifest Verification Tests
# =============================================================================


def test_bug_manifest_structure_and_counts() -> None:
    """Verify bug dataset manifest exists, contains 60-80 samples, and covers all required categories."""
    manifest_path = ROOT_DIR / "tests" / "bug_samples" / "manifest.json"
    assert manifest_path.is_file(), "Missing tests/bug_samples/manifest.json"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["dataset"] == "bug_samples"
    samples = data["samples"]
    total = len(samples)

    # Specification requires 60–80 samples
    assert 60 <= total <= 80, f"Expected 60-80 bug samples, got {total}"

    categories = {s["category"] for s in samples}
    expected_categories = {
        "syntax_error",
        "standard_exception",
        "logic_bug",
        "clean_program",
        "external_dependency",
        "runtime_fault",
        "initial_slm",
    }
    assert expected_categories.issubset(categories), f"Missing categories: {expected_categories - categories}"

    # Verify every referenced file exists on disk
    for s in samples:
        rel_path = s["path"]
        target = ROOT_DIR / "tests" / "bug_samples" / rel_path
        assert target.is_file(), f"Referenced bug sample file does not exist: {target}"


def test_complexity_manifest_structure_and_counts() -> None:
    """Verify complexity dataset manifest exists, contains 35-50 functions, and covers all classes."""
    manifest_path = ROOT_DIR / "tests" / "complexity_samples" / "manifest.json"
    assert manifest_path.is_file(), "Missing tests/complexity_samples/manifest.json"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["dataset"] == "complexity_samples"
    samples = data["samples"]
    total = len(samples)

    # Specification requires 35–50 functions
    assert 35 <= total <= 50, f"Expected 35-50 complexity functions, got {total}"

    # Verify time complexity classes represented
    time_classes = {s["expected_time"] for s in samples if s["expected_time"] is not None}
    required_classes = {"O(1)", "O(n)", "O(n log n)", "O(n^2)", "O(nm)", "O(log n)", "O(2^n)"}
    assert required_classes.issubset(time_classes), f"Missing complexity classes: {required_classes - time_classes}"

    # Verify abstention cases are included
    abstaining = [s for s in samples if s["should_abstain"]]
    assert len(abstaining) >= 5, "Expected at least 5 sound abstention fixtures"

    # Verify every referenced file exists on disk
    for s in samples:
        rel_path = s["path"]
        target = ROOT_DIR / "tests" / "complexity_samples" / rel_path
        assert target.is_file(), f"Referenced complexity file does not exist: {target}"


def test_profiling_manifest_structure_and_inputs() -> None:
    """Verify profiling dataset manifest exists, covers all benchmark types, and input files exist."""
    manifest_path = ROOT_DIR / "tests" / "profiling_samples" / "manifest.json"
    assert manifest_path.is_file(), "Missing tests/profiling_samples/manifest.json"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["dataset"] == "profiling_samples"
    samples = data["samples"]
    assert len(samples) >= 15, f"Expected at least 15 profiling benchmarks, got {len(samples)}"

    categories = {s["category"] for s in samples}
    expected_categories = {
        "pure_timing",
        "pure_computation",
        "stateful",
        "mutating",
        "memory_intensive",
        "slow_import",
        "failing_target",
    }
    assert expected_categories.issubset(categories), f"Missing categories: {expected_categories - categories}"

    # Verify targets and JSON inputs exist on disk and inputs are valid JSON
    for s in samples:
        target = ROOT_DIR / "tests" / "profiling_samples" / s["target_file"]
        assert target.is_file(), f"Target file missing: {target}"

        input_f = ROOT_DIR / "tests" / "profiling_samples" / s["input_file"]
        assert input_f.is_file(), f"Input file missing: {input_f}"
        input_data = json.loads(input_f.read_text(encoding="utf-8"))
        assert isinstance(input_data, dict), f"Input file {input_f} must be a JSON object"


def test_boundary_manifest_structure_and_categories() -> None:
    """Verify boundary dataset manifest exists and covers required OS/filesystem boundary conditions."""
    manifest_path = ROOT_DIR / "tests" / "boundary_samples" / "manifest.json"
    assert manifest_path.is_file(), "Missing tests/boundary_samples/manifest.json"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["dataset"] == "boundary_samples"
    samples = data["samples"]
    assert len(samples) >= 20, f"Expected at least 20 boundary fixtures, got {len(samples)}"

    categories = {s["category"] for s in samples}
    expected_categories = {
        "line_endings",
        "encodings",
        "filenames",
        "permissions",
        "detection",
        "processes",
    }
    assert expected_categories.issubset(categories), f"Missing boundary categories: {expected_categories - categories}"

    for s in samples:
        target = ROOT_DIR / "tests" / "boundary_samples" / s["path"]
        assert target.is_file(), f"Boundary fixture missing: {target}"


# =============================================================================
# Automated Harness Execution Tests (Fast Subset)
# =============================================================================


def test_evaluate_bug_dataset_fast() -> None:
    """Verify evaluate_bug_dataset executes offline with 100% schema validity and zero process leaks."""
    res = evaluate_bug_dataset(fast=True)
    assert res.total >= 7
    assert res.passed == res.total, f"Bug samples failed: {[s for s in res.samples if not s.passed]}"
    assert res.schema_valid_count == res.total
    assert res.lingering_children_detected == 0


def test_evaluate_complexity_dataset_fast() -> None:
    """Verify evaluate_complexity_dataset executes offline with 100% schema validity."""
    res = evaluate_complexity_dataset(fast=True)
    assert res.total >= 10
    assert res.passed == res.total, f"Complexity functions failed: {[s for s in res.samples if not s.passed]}"
    assert res.schema_valid_count == res.total
    assert res.lingering_children_detected == 0


def test_evaluate_profiling_dataset_fast() -> None:
    """Verify evaluate_profiling_dataset executes offline with separated metrics and zero process leaks."""
    res = evaluate_profiling_dataset(fast=True)
    assert res.total >= 5
    assert res.passed == res.total, f"Profiling benchmarks failed: {[s for s in res.samples if not s.passed]}"
    assert res.schema_valid_count == res.total
    assert res.lingering_children_detected == 0


def test_evaluate_boundary_dataset_fast() -> None:
    """Verify evaluate_boundary_dataset executes offline with 100% pass rate."""
    res = evaluate_boundary_dataset(fast=True)
    assert res.total >= 10
    assert res.passed == res.total, f"Boundary fixtures failed: {[s for s in res.samples if not s.passed]}"
    assert res.schema_valid_count == res.total
    assert res.lingering_children_detected == 0


def test_run_full_evaluation_fast_report() -> None:
    """Verify run_full_evaluation produces complete structured EvaluationReport."""
    report = run_full_evaluation(selected_suite="all", fast=True)
    assert report.total_samples >= 30
    assert report.total_passed == report.total_samples
    assert report.total_failed == 0
    assert report.overall_accuracy_pct == 100.0
    assert report.zero_process_leaks is True
    assert "os" in report.environment
    assert "python" in report.toolchain
