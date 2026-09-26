"""End-to-end integration tests for the `localdev profile` CLI command (Phase 11 Task P11-T4).

Verifies:
1. End-to-end execution of `localdev profile` for pure, mutating, stateful, and heavy functions.
2. Formats: human-readable terminal output and structured JSON envelope.
3. Strict metric separation:
   - Import cost (duration, stdout, stderr)
   - Invocation latency (warm-up, measured, median, mean, min, max, dispersion)
   - Python heap allocations (tracemalloc peak bytes)
   - Worker process-tree approximate peak RSS (OS-level resident memory)
   - Hot-process repeated invocation semantics vs fresh startup costs
4. Error handling and exit codes:
   - Target function exception -> EXIT_TARGET_FAILURE (1)
   - Target import failure -> EXIT_TARGET_FAILURE (1)
   - Missing or invalid selector -> EXIT_CLI_USAGE_ERROR (2)
   - Malformed input file -> EXIT_CLI_USAGE_ERROR (2)
   - Execution timeout -> EXIT_TIMEOUT_RESOURCE_BREACH (4)
5. Worker process-tree termination and cleanup.
"""

from __future__ import annotations

import io
import json
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import psutil
import pytest

from localdev.cli import main
from localdev.constants import (
    EXIT_CLI_USAGE_ERROR,
    EXIT_SUCCESS,
    EXIT_TARGET_FAILURE,
    EXIT_TIMEOUT_RESOURCE_BREACH,
)

pytestmark = pytest.mark.windows

TARGETS_DIR = Path("tests/profiling_samples/targets")
INPUTS_DIR = Path("tests/profiling_samples/inputs")


# =============================================================================
# Helper Utilities
# =============================================================================


def run_cli(args: list[str]) -> tuple[int, str, str]:
    """Execute localdev CLI with captured stdout and stderr streams."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        exit_code = main(args)
    return exit_code, out_buf.getvalue(), err_buf.getvalue()


# =============================================================================
# Happy Path Tests: Terminal & JSON Output
# =============================================================================


def test_profile_command_pure_function_terminal() -> None:
    """Verify terminal output formatting and distinct metrics for pure function."""
    target = f"{TARGETS_DIR / 'timing_samples.py'}::compute_squares"
    exit_code, stdout, stderr = run_cli(["profile", target])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    assert stderr == ""

    # Verify all expected terminal output sections
    assert "--- Function Profile:" in stdout
    assert "Import Cost:" in stdout
    assert "Median Latency:" in stdout
    assert "dispersion:" in stdout
    assert "Python Heap (Peak):" in stdout
    assert "tracemalloc-tracked" in stdout
    assert "Worker Process RSS:" in stdout
    assert "peak process tree" in stdout
    assert "Iterations:            2 warm-up, 7 measured" in stdout
    assert "Execution Semantics:   Hot process (module state persistent)" in stdout


def test_profile_command_pure_function_json() -> None:
    """Verify structured JSON envelope output for pure function profiling."""
    target = f"{TARGETS_DIR / 'timing_samples.py'}::compute_squares"
    exit_code, stdout, stderr = run_cli(["profile", target, "--json"])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    assert stderr == ""

    payload = json.loads(stdout)
    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "profile"
    assert payload["success"] is True
    assert payload["target_path"] == target

    data = payload["data"]
    assert data["target"] == target
    assert data["import_duration_ms"] >= 0.0
    assert data["warmup_invocations"] == 2
    assert data["measured_invocations"] == 7
    assert data["median_latency_ms"] >= 0.0
    assert data["mean_latency_ms"] >= 0.0
    assert data["min_latency_ms"] <= data["median_latency_ms"] <= data["max_latency_ms"]
    assert data["dispersion_ms"] >= 0.0
    assert data["python_allocations_tracemalloc_bytes"] >= 0
    assert data["approximate_process_tree_rss_bytes"] > 0
    assert data["hot_process_reused"] is True


def test_profile_command_custom_warmup_and_measured() -> None:
    """Verify custom warmup and measured flags are respected in report."""
    target = f"{TARGETS_DIR / 'timing_samples.py'}::compute_squares"
    exit_code, stdout, stderr = run_cli([
        "profile",
        target,
        "--warmup",
        "4",
        "--measured",
        "10",
        "--json",
    ])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    payload = json.loads(stdout)
    data = payload["data"]
    assert data["warmup_invocations"] == 4
    assert data["measured_invocations"] == 10


def test_profile_command_with_json_input(tmp_path: Path) -> None:
    """Verify passing user arguments via --input file."""
    target = f"{TARGETS_DIR / 'timing_samples.py'}::quick_add"
    input_file = tmp_path / "args.json"
    input_file.write_text('{"args": [15, 25]}', encoding="utf-8")

    exit_code, stdout, stderr = run_cli(["profile", target, "--input", str(input_file), "--json"])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    payload = json.loads(stdout)
    assert payload["success"] is True
    assert payload["data"]["measured_invocations"] == 7


# =============================================================================
# Behavioral & Semantic Tests: Mutating, Stateful, & Memory Scaling
# =============================================================================


def test_profile_command_mutating_target(tmp_path: Path) -> None:
    """Verify mutating function receives fresh unmutated arguments on each run."""
    sample_file = tmp_path / "mutating_sample.py"
    sample_file.write_text(
        "def pop_and_sort(items: list[int]) -> int:\n"
        "    items.sort()\n"
        "    return items.pop(0)\n",
        encoding="utf-8",
    )
    input_file = tmp_path / "list_in.json"
    input_file.write_text('{"args": [[9, 3, 7, 1, 5]]}', encoding="utf-8")

    target = f"{sample_file}::pop_and_sort"
    exit_code, stdout, stderr = run_cli(["profile", target, "--input", str(input_file), "--json"])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    payload = json.loads(stdout)
    assert payload["success"] is True
    assert payload["data"]["measured_invocations"] == 7


def test_profile_command_stateful_hot_process_persistence(tmp_path: Path) -> None:
    """Verify module-level state persists across invocations under hot-process semantics."""
    target = f"{TARGETS_DIR / 'stateful_samples.py'}::record_history"
    input_file = tmp_path / "state_in.json"
    input_file.write_text('{"args": [100]}', encoding="utf-8")

    exit_code, stdout, stderr = run_cli(["profile", target, "--input", str(input_file), "--json"])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    payload = json.loads(stdout)
    assert payload["success"] is True
    assert payload["data"]["hot_process_reused"] is True


def test_profile_command_distinct_metrics_not_conflated() -> None:
    """Verify tracemalloc heap allocations and process tree RSS are strictly distinct."""
    target = f"{TARGETS_DIR / 'memory_samples.py'}::allocate_two_mb"
    exit_code, stdout, stderr = run_cli(["profile", target, "--json"])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    payload = json.loads(stdout)
    data = payload["data"]

    heap_bytes = data["python_allocations_tracemalloc_bytes"]
    rss_bytes = data["approximate_process_tree_rss_bytes"]

    # 2 MB allocation on Python heap
    assert heap_bytes >= 2 * 1024 * 1024
    assert heap_bytes < 3 * 1024 * 1024

    # Process RSS includes interpreter, libraries, runtime (~20-60 MB)
    assert rss_bytes >= 15 * 1024 * 1024
    assert rss_bytes > heap_bytes


def test_profile_command_slow_import(tmp_path: Path) -> None:
    """Verify target with slow import-time execution accurately isolates import duration."""
    target_file = tmp_path / "slow_import_sample.py"
    target_file.write_text(
        "import time\n"
        "time.sleep(0.050)\n"
        "def compute() -> int:\n"
        "    return 42\n",
        encoding="utf-8",
    )
    target = f"{target_file}::compute"
    exit_code, stdout, stderr = run_cli(["profile", target, "--json"])

    assert exit_code == EXIT_SUCCESS, f"Stderr: {stderr}"
    payload = json.loads(stdout)
    assert payload["success"] is True
    # Import duration must be at least ~40ms
    assert payload["data"]["import_duration_ms"] >= 40.0
    # Function latency itself is fast (< 10ms)
    assert payload["data"]["median_latency_ms"] < 10.0


# =============================================================================
# Error Handling & Exit Code Tests
# =============================================================================


def test_profile_command_missing_selector() -> None:
    """Verify missing function selector exits with EXIT_CLI_USAGE_ERROR (2)."""
    target_file = str(TARGETS_DIR / "timing_samples.py")
    exit_code, _stdout, stderr = run_cli(["profile", target_file])

    assert exit_code == EXIT_CLI_USAGE_ERROR
    assert "requires an explicit function or method selector" in stderr


def test_profile_command_missing_selector_json() -> None:
    """Verify missing function selector with --json outputs valid error envelope."""
    target_file = str(TARGETS_DIR / "timing_samples.py")
    exit_code, stdout, _stderr = run_cli(["profile", target_file, "--json"])

    assert exit_code == EXIT_CLI_USAGE_ERROR
    payload = json.loads(stdout)
    assert payload["success"] is False
    assert any("requires an explicit function or method selector" in e for e in payload["errors"])


def test_profile_command_nonexistent_selector() -> None:
    """Verify nonexistent selector exits with EXIT_CLI_USAGE_ERROR (2)."""
    target = f"{TARGETS_DIR / 'timing_samples.py'}::nonexistent_function"
    exit_code, _stdout, stderr = run_cli(["profile", target])

    assert exit_code == EXIT_CLI_USAGE_ERROR
    assert "Function selector 'nonexistent_function' not found" in stderr


def test_profile_command_import_error(tmp_path: Path) -> None:
    """Verify target import failure exits with EXIT_TARGET_FAILURE (1)."""
    target_file = tmp_path / "broken_import.py"
    target_file.write_text(
        "raise ImportError('Module import exploded!')\n"
        "def run() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    target = f"{target_file}::run"
    exit_code, _stdout, stderr = run_cli(["profile", target])

    assert exit_code == EXIT_TARGET_FAILURE
    assert "Module import exploded!" in stderr


def test_profile_command_function_invocation_exception() -> None:
    """Verify function exception during execution exits with EXIT_TARGET_FAILURE (1)."""
    target = f"{TARGETS_DIR / 'failing_samples.py'}::raise_zero_division"
    exit_code, _stdout, stderr = run_cli(["profile", target])

    assert exit_code == EXIT_TARGET_FAILURE
    assert "ZeroDivisionError" in stderr


def test_profile_command_timeout(tmp_path: Path) -> None:
    """Verify execution timeout exits with EXIT_TIMEOUT_RESOURCE_BREACH (4)."""
    target_file = tmp_path / "hang_target.py"
    target_file.write_text(
        "import time\n"
        "def infinite_loop() -> None:\n"
        "    while True:\n"
        "        time.sleep(0.1)\n",
        encoding="utf-8",
    )
    target = f"{target_file}::infinite_loop"
    exit_code, _stdout, stderr = run_cli(["profile", target, "--timeout", "1.0"])

    assert exit_code == EXIT_TIMEOUT_RESOURCE_BREACH
    assert "timed out after 1.0 seconds" in stderr


def test_profile_command_invalid_json_input(tmp_path: Path) -> None:
    """Verify malformed JSON input file exits with EXIT_CLI_USAGE_ERROR (2)."""
    target = f"{TARGETS_DIR / 'timing_samples.py'}::compute_squares"
    bad_input = tmp_path / "bad.json"
    bad_input.write_text('{"args": [1, 2, }', encoding="utf-8")

    exit_code, _stdout, stderr = run_cli(["profile", target, "--input", str(bad_input)])

    assert exit_code == EXIT_CLI_USAGE_ERROR
    assert "Malformed JSON" in stderr


# =============================================================================
# Process Tree Termination & Cleanup Test
# =============================================================================


def test_profile_command_process_tree_cleanup() -> None:
    """Verify no lingering worker processes remain active after profiling completes."""
    target = f"{TARGETS_DIR / 'timing_samples.py'}::compute_squares"

    # Capture initial process snapshot
    current_proc = psutil.Process()
    initial_children = set(current_proc.children(recursive=True))

    exit_code, _stdout, _stderr = run_cli(["profile", target])
    assert exit_code == EXIT_SUCCESS

    # Give OS a brief moment to finish reaping terminated subprocesses
    time.sleep(0.1)

    final_children = set(current_proc.children(recursive=True))
    new_lingering_children = final_children - initial_children

    assert not new_lingering_children, f"Lingering worker processes detected: {new_lingering_children}"
