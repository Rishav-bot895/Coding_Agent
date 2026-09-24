"""Windows execution limits tests for subprocess output capture and timeouts.

Verifies:
- Normal termination and non-zero exit codes.
- Timeout enforcement for runaway loops.
- Output byte cap enforcement for stdout and stderr floods.
- Concurrent pipe draining preventing pipe buffer deadlocks.
- Bounded parent memory consumption during output floods.
- Non-deadlocking behavior on blocking stdin reads.
"""

from __future__ import annotations

import os
from pathlib import Path

import psutil
import pytest

from localdev.execution.limits import ExecutionLimits
from localdev.execution.runner import build_execution_request, run_execution_request

pytestmark = [pytest.mark.windows]

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "bug_samples" / "runtime"


def test_normal_termination() -> None:
    """Target completes normally, capturing stdout with exit code 0."""
    target = FIXTURES_DIR / "clean_exit.py"
    req = build_execution_request(target_path=target)
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.output_truncated is False
    assert "Hello from clean exit" in result.stdout
    assert result.stderr == ""


def test_nonzero_exit() -> None:
    """Target exiting with non-zero status preserves stderr and exit code."""
    target = FIXTURES_DIR / "nonzero_exit.py"
    req = build_execution_request(target_path=target)
    result = run_execution_request(req)

    assert result.exit_code == 42
    assert result.timed_out is False
    assert result.output_truncated is False
    assert "Fatal runtime error occurred" in result.stderr


def test_infinite_loop_timeout() -> None:
    """Target trapped in infinite loop terminates cleanly upon wall-clock timeout."""
    target = FIXTURES_DIR / "infinite_loop.py"
    req = build_execution_request(target_path=target)
    limits = ExecutionLimits(timeout_seconds=0.5)

    result = run_execution_request(req, limits=limits)

    assert result.timed_out is True
    assert result.output_truncated is False
    assert result.duration_seconds >= 0.45


def test_stdout_flood_byte_cap() -> None:
    """Subprocess producing massive stdout is truncated at configured byte cap."""
    target = FIXTURES_DIR / "stdout_flood.py"
    req = build_execution_request(target_path=target)
    limits = ExecutionLimits(timeout_seconds=5.0, output_byte_cap=10 * 1024)

    result = run_execution_request(req, limits=limits)

    assert result.output_truncated is True
    assert result.timed_out is False
    total_bytes = len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8"))
    assert total_bytes <= 10 * 1024
    assert len(result.stdout) > 0


def test_default_512kb_byte_cap() -> None:
    """Default 512 KB output cap is enforced on large stdout floods."""
    target = FIXTURES_DIR / "stdout_flood.py"
    req = build_execution_request(target_path=target)
    limits = ExecutionLimits(timeout_seconds=5.0)  # Default 512 KB

    result = run_execution_request(req, limits=limits)

    assert result.output_truncated is True
    total_bytes = len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8"))
    assert total_bytes <= 512 * 1024
    assert len(result.stdout) > 0


def test_stderr_flood_byte_cap() -> None:
    """Subprocess producing massive stderr is truncated at configured byte cap."""
    target = FIXTURES_DIR / "stderr_flood.py"
    req = build_execution_request(target_path=target)
    limits = ExecutionLimits(timeout_seconds=5.0, output_byte_cap=16 * 1024)

    result = run_execution_request(req, limits=limits)

    assert result.output_truncated is True
    assert result.timed_out is False
    total_bytes = len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8"))
    assert total_bytes <= 16 * 1024
    assert len(result.stderr) > 0


def test_mixed_output_concurrent_draining() -> None:
    """Interleaved stdout and stderr drain concurrently without pipe deadlocks."""
    target = FIXTURES_DIR / "mixed_output.py"
    req = build_execution_request(target_path=target)
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.output_truncated is False
    assert "out 0" in result.stdout
    assert "out 99" in result.stdout
    assert "err 0" in result.stderr
    assert "err 99" in result.stderr


def test_blocking_stdin_reads_timeout() -> None:
    """Target blocking on stdin terminates cleanly via timeout without parent hang."""
    target = FIXTURES_DIR / "blocking_stdin.py"
    req = build_execution_request(target_path=target)
    limits = ExecutionLimits(timeout_seconds=0.5)

    result = run_execution_request(req, limits=limits)

    assert result.timed_out is True
    assert result.output_truncated is False


def test_parent_memory_bounded_during_flood() -> None:
    """Parent process memory does not balloon when child floods megabytes of output."""
    target = FIXTURES_DIR / "stdout_flood.py"
    req = build_execution_request(target_path=target)
    limits = ExecutionLimits(timeout_seconds=5.0, output_byte_cap=32 * 1024)

    current_proc = psutil.Process(os.getpid())
    rss_before = current_proc.memory_info().rss

    result = run_execution_request(req, limits=limits)

    rss_after = current_proc.memory_info().rss
    rss_growth = rss_after - rss_before

    assert result.output_truncated is True
    # The flood script attempts to produce 2 MB of output.
    # Parent RSS growth must stay bounded well below the 2 MB payload.
    assert rss_growth < 1024 * 1024  # Less than 1 MB growth
