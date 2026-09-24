"""Integration tests for Windows Job Object and psutil fallback runner policy.

Verifies:
1. Native Windows Job Object execution records execution_backend == "windows_job".
2. Multi-process tree timeout termination cleanly kills descendants under windows_job.
3. Explicit prefer_job_object=False runs under psutil_fallback backend.
4. Simulated nested job assignment restriction (ERROR_ACCESS_DENIED) falls back cleanly
   to psutil_fallback backend with zero interruption to target execution.
5. Rigorous QA on psutil_fallback path: multi-level process trees (root, child,
   grandchild) are fully discovered and terminated on timeout under psutil fallback alone.
6. Strict mode (fail_on_job_failure=True) aborts execution and terminates the spawned
   process when Job Object assignment fails.
7. Strict mode (fail_on_job_failure=True) aborts execution when Job Object creation fails.
8. CLI integration: 'localdev debug' seamlessly succeeds via fallback when unflagged,
   but aborts with failure code when --fail-on-job-failure is specified.
9. JSON and Terminal reporting include the active execution_backend.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from localdev.cli import main
from localdev.constants import EXIT_SUCCESS, EXIT_TARGET_FAILURE
from localdev.errors import JobObjectAssignmentError, JobObjectCreationError
from localdev.execution.limits import ExecutionLimits
from localdev.execution.process_tree import is_process_running
from localdev.execution.runner import build_execution_request, run_execution_request
from localdev.execution.windows_job import (
    ERROR_ACCESS_DENIED,
    WindowsJobObject,
    get_kernel32,
)

pytestmark = [pytest.mark.windows]

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "boundary_samples" / "processes"


def _wait_for_pids(pid_file: Path, expected_count: int, timeout: float = 5.0) -> list[int]:
    """Poll a PID recording file until expected number of PIDs are present."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if pid_file.exists():
            lines = [
                line.strip()
                for line in pid_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(lines) >= expected_count:
                pids: list[int] = []
                for line in lines:
                    _, pid_str = line.split(":", 1)
                    pids.append(int(pid_str))
                return pids
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {expected_count} PIDs in {pid_file}")


def test_job_runner_un_nested_execution_success(tmp_path: Path) -> None:
    """Standard un-nested execution on Windows uses windows_job backend and succeeds."""
    script = tmp_path / "hello.py"
    script.write_text("print('hello windows job')\n", encoding="utf-8")

    req = build_execution_request(target_path=script)
    limits = ExecutionLimits(prefer_job_object=True)

    result = run_execution_request(req, limits=limits)

    assert result.exit_code == 0
    assert result.execution_backend == "windows_job"
    assert "hello windows job" in result.stdout
    assert result.timed_out is False


def test_job_runner_un_nested_multi_process_tree_timeout(tmp_path: Path) -> None:
    """Subprocess timeout under windows_job backend terminates root, child, and grandchild."""
    pid_file = tmp_path / "job_tree_pids.txt"
    script = FIXTURES_DIR / "spawn_tree.py"

    req = build_execution_request(
        target_path=script,
        args=["--pid-file", str(pid_file)],
    )
    limits = ExecutionLimits(timeout_seconds=1.2, prefer_job_object=True)

    result = run_execution_request(req, limits=limits)

    assert result.timed_out is True
    assert result.execution_backend == "windows_job"

    # Verify descendant processes were spawned and recorded
    assert pid_file.exists()
    pids = _wait_for_pids(pid_file, expected_count=2, timeout=2.0)
    assert len(pids) >= 2

    # Verify all captured processes were killed
    time.sleep(0.1)
    for pid in pids:
        assert is_process_running(pid) is False, f"Process {pid} should have been terminated"


def test_job_runner_prefer_job_object_false_runs_under_psutil(tmp_path: Path) -> None:
    """When prefer_job_object=False, runner uses psutil_fallback backend."""
    script = tmp_path / "psutil_pref.py"
    script.write_text("print('psutil preferred')\n", encoding="utf-8")

    req = build_execution_request(target_path=script)
    limits = ExecutionLimits(prefer_job_object=False)

    result = run_execution_request(req, limits=limits)

    assert result.exit_code == 0
    assert result.execution_backend == "psutil_fallback"
    assert "psutil preferred" in result.stdout


def test_job_runner_nested_job_assignment_failure_graceful_fallback(tmp_path: Path) -> None:
    """Simulated nested job assignment failure seamlessly falls back to psutil."""
    script = tmp_path / "fallback.py"
    script.write_text("print('graceful fallback active')\n", encoding="utf-8")

    req = build_execution_request(target_path=script)
    limits = ExecutionLimits(prefer_job_object=True, fail_on_job_failure=False)

    k32 = get_kernel32()
    with (
        patch.object(k32, "AssignProcessToJobObject", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
    ):
        result = run_execution_request(req, limits=limits)

    assert result.exit_code == 0
    assert result.execution_backend == "psutil_fallback"
    assert "graceful fallback active" in result.stdout
    assert result.timed_out is False


def test_job_runner_psutil_fallback_multi_process_tree_timeout(tmp_path: Path) -> None:
    """Rigorously test psutil_fallback path: cleans up child/grandchild on timeout.

    This ensures developer IDE environments (such as VS Code integrated terminal)
    where outer jobs prevent Job Object assignment receive full process-tree
    termination guarantees under the psutil fallback alone.
    """
    pid_file = tmp_path / "psutil_tree_pids.txt"
    script = FIXTURES_DIR / "spawn_tree.py"

    req = build_execution_request(
        target_path=script,
        args=["--pid-file", str(pid_file)],
    )
    limits = ExecutionLimits(timeout_seconds=1.2, fail_on_job_failure=False)

    k32 = get_kernel32()
    with (
        patch.object(k32, "AssignProcessToJobObject", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
    ):
        result = run_execution_request(req, limits=limits)

    assert result.timed_out is True
    assert result.execution_backend == "psutil_fallback"

    assert pid_file.exists()
    pids = _wait_for_pids(pid_file, expected_count=2, timeout=2.0)
    assert len(pids) >= 2

    time.sleep(0.1)
    for pid in pids:
        assert is_process_running(pid) is False, f"Process {pid} should have been terminated by psutil fallback"


def test_job_runner_strict_mode_aborts_on_assignment_failure(tmp_path: Path) -> None:
    """fail_on_job_failure=True aborts and terminates target when assignment fails."""
    script = tmp_path / "sleep_forever.py"
    script.write_text("import time; time.sleep(60)\n", encoding="utf-8")

    req = build_execution_request(target_path=script)
    limits = ExecutionLimits(prefer_job_object=True, fail_on_job_failure=True)

    k32 = get_kernel32()
    with (
        patch.object(k32, "AssignProcessToJobObject", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
        pytest.raises(JobObjectAssignmentError) as exc_info,
    ):
        run_execution_request(req, limits=limits)

    assert exc_info.value.is_nested_restriction is True
    assert exc_info.value.win_error_code == ERROR_ACCESS_DENIED


def test_job_runner_strict_mode_aborts_on_creation_failure(tmp_path: Path) -> None:
    """fail_on_job_failure=True aborts when Job Object creation fails."""
    script = tmp_path / "noop.py"
    script.write_text("print('noop')\n", encoding="utf-8")

    req = build_execution_request(target_path=script)
    k32 = get_kernel32()

    # With fail_on_job_failure=True: raises JobObjectCreationError
    with (
        patch.object(k32, "CreateJobObjectW", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
        pytest.raises(JobObjectCreationError),
    ):
        run_execution_request(req, limits=ExecutionLimits(fail_on_job_failure=True))

    # With fail_on_job_failure=False: falls back to psutil_fallback
    with (
        patch.object(k32, "CreateJobObjectW", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
    ):
        result = run_execution_request(req, limits=ExecutionLimits(fail_on_job_failure=False))
        assert result.exit_code == 0
        assert result.execution_backend == "psutil_fallback"


def test_cli_debug_fail_on_job_failure_flag(tmp_path: Path) -> None:
    """CLI 'localdev debug' aborts when --fail-on-job-failure is passed and assignment fails."""
    script = tmp_path / "cli_test.py"
    script.write_text("print('cli running')\n", encoding="utf-8")

    k32 = get_kernel32()

    # 1. Without --fail-on-job-failure: succeeds via seamless psutil fallback
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with (
        patch.object(k32, "AssignProcessToJobObject", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
        redirect_stdout(stdout_buf),
        redirect_stderr(stderr_buf),
    ):
        code = main(["debug", str(script)])

    assert code == EXIT_SUCCESS
    assert "cli running" in stdout_buf.getvalue()
    assert "Backend:               psutil_fallback" in stdout_buf.getvalue()

    # 2. With --fail-on-job-failure: aborts with error message and exit code 1
    stdout_fail = io.StringIO()
    stderr_fail = io.StringIO()
    with (
        patch.object(k32, "AssignProcessToJobObject", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
        redirect_stdout(stdout_fail),
        redirect_stderr(stderr_fail),
    ):
        fail_code = main(["debug", str(script), "--fail-on-job-failure"])

    assert fail_code == EXIT_TARGET_FAILURE
    err_out = stderr_fail.getvalue()
    assert "Failed to assign process" in err_out


def test_cli_debug_json_reports_execution_backend(tmp_path: Path) -> None:
    """CLI 'localdev debug --json' records execution_backend in JSON envelope."""
    script = tmp_path / "json_backend.py"
    script.write_text("print('backend check')\n", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["debug", "--json", str(script)])

    assert code == EXIT_SUCCESS
    envelope = json.loads(stdout.getvalue())
    assert envelope["command"] == "debug"
    assert envelope["success"] is True
    assert envelope["data"]["execution_backend"] == "windows_job"
