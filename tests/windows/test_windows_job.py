"""Windows Job Object lifecycle, handle management, and containment tests.

Verifies:
1. Native Win32 Job Object creation and deterministic handle closure.
2. KILL_ON_JOB_CLOSE terminating a target process upon job handle closure.
3. KILL_ON_JOB_CLOSE terminating an entire process tree (parent, child, grandchild).
4. TerminateJobObject explicit termination method.
5. Zero handle leaks across repeated Job Object creation/destruction cycles.
6. Error handling: closed job assignment, non-existent PID assignment.
7. Nested Job Object handling and ERROR_ACCESS_DENIED translation.
8. Breakaway flag limit configuration.
"""

from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import patch

import psutil
import pytest

from localdev.errors import (
    JobObjectAssignmentError,
    JobObjectError,
)
from localdev.execution.windows_job import (
    ERROR_ACCESS_DENIED,
    WindowsJobObject,
    can_create_job_object,
)

pytestmark = [pytest.mark.windows]


def test_job_object_creation_and_closure() -> None:
    """Job Object creates a valid handle and closes it deterministically and idempotently."""
    job = WindowsJobObject()
    assert job.handle is not None
    assert job.handle > 0
    assert job.is_closed is False

    job.close()
    assert job.handle is None
    assert job.is_closed is True

    # Idempotent close
    job.close()
    assert job.is_closed is True


def test_job_object_context_manager() -> None:
    """Job Object works cleanly as a context manager."""
    with WindowsJobObject() as job:
        assert job.handle is not None
        assert not job.is_closed
    assert job.is_closed


def test_can_create_job_object() -> None:
    """Environment probe confirms Job Object creation support."""
    assert can_create_job_object() is True


def test_kill_on_job_close_terminates_single_process() -> None:
    """Closing job handle terminates the assigned process via KILL_ON_JOB_CLOSE."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with WindowsJobObject(kill_on_close=True) as job:
            job.assign_process(proc)
            assert job.is_process_in_job(proc) is True

        # Job closed: process must be killed by the Windows kernel
        time.sleep(0.5)
        poll_res = proc.poll()
        assert poll_res is not None, "Process should be terminated after job closure"
        assert not psutil.pid_exists(proc.pid)
    finally:
        if psutil.pid_exists(proc.pid):
            proc.kill()


def test_kill_on_job_close_terminates_process_tree() -> None:
    """Closing job handle terminates the entire process tree (parent, child, grandchild)."""
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import subprocess, sys, time; "
        "gc = subprocess.Popen([sys.executable, \\'-c\\', \\'import time; time.sleep(30)\\']); "
        "time.sleep(30)'])\n"
        "time.sleep(30)\n"
    )

    parent = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        with WindowsJobObject(kill_on_close=True) as job:
            job.assign_process(parent)

            # Wait for child and grandchild processes to spawn
            descendant_pids: list[int] = []
            start = time.perf_counter()
            while time.perf_counter() - start < 5.0:
                try:
                    p = psutil.Process(parent.pid)
                    children = p.children(recursive=True)
                    if len(children) >= 2:
                        descendant_pids = [c.pid for c in children]
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                time.sleep(0.1)

            assert len(descendant_pids) >= 2, "Expected at least child and grandchild processes"

        # Exiting context closes the job handle
        time.sleep(0.5)

        # Assert all processes in the tree were terminated
        all_pids = [parent.pid, *descendant_pids]
        alive_pids = [pid for pid in all_pids if psutil.pid_exists(pid)]
        assert alive_pids == [], f"All processes should be dead, but these survived: {alive_pids}"
    finally:
        if psutil.pid_exists(parent.pid):
            parent.kill()


def test_job_object_terminate_method() -> None:
    """Explicitly calling terminate() kills assigned processes immediately."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with WindowsJobObject() as job:
            job.assign_process(proc)
            job.terminate(exit_code=42)
            time.sleep(0.3)
            assert not psutil.pid_exists(proc.pid)
    finally:
        if psutil.pid_exists(proc.pid):
            proc.kill()


def test_handle_leak_prevention_across_cycles() -> None:
    """Repeated Job Object creation and destruction does not leak OS handles."""
    current_proc = psutil.Process()
    # Warm up ctypes and DLL references
    with WindowsJobObject():
        pass

    initial_handles = current_proc.num_handles()

    # Run 50 cycles
    for _ in range(50):
        with WindowsJobObject():
            pass

    final_handles = current_proc.num_handles()
    # Handle count should return to initial (allow minimal transient fluctuation <= 3)
    handle_delta = final_handles - initial_handles
    assert handle_delta <= 3, f"Handle leak detected: handle count grew by {handle_delta}"


def test_assign_to_closed_job_raises() -> None:
    """Assigning to an already-closed job raises JobObjectError."""
    job = WindowsJobObject()
    job.close()
    with pytest.raises(JobObjectError) as exc_info:
        job.assign_process(1234)
    assert "handle is closed" in str(exc_info.value)


def test_assign_nonexistent_pid_raises() -> None:
    """Assigning a non-existent PID raises JobObjectAssignmentError without leaking handles."""
    with WindowsJobObject() as job:
        # Non-existent high PID
        with pytest.raises(JobObjectAssignmentError) as exc_info:
            job.assign_process(999999)
        assert "Failed to open process 999999" in str(exc_info.value)


def test_nested_job_assignment_supported() -> None:
    """Processes can be assigned to multiple Job Objects in standard Windows 11 nested jobs."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with WindowsJobObject() as job1, WindowsJobObject() as job2:
            job1.assign_process(proc)
            assert job1.is_process_in_job(proc) is True

            job2.assign_process(proc)
            assert job2.is_process_in_job(proc) is True
    finally:
        if psutil.pid_exists(proc.pid):
            proc.kill()


def test_nested_job_restriction_translation() -> None:
    """ERROR_ACCESS_DENIED on AssignProcessToJobObject translates to is_nested_restriction=True."""
    with (
        WindowsJobObject() as job,
        patch.object(job._kernel32, "AssignProcessToJobObject", return_value=0),
        patch("ctypes.get_last_error", return_value=ERROR_ACCESS_DENIED),
        pytest.raises(JobObjectAssignmentError) as exc_info,
    ):
        # Use current process PID
        job.assign_process(psutil.Process().pid)

    err = exc_info.value
    assert err.is_nested_restriction is True
    assert err.win_error_code == ERROR_ACCESS_DENIED
    assert "nested" in str(err)


def test_breakaway_flags_configuration() -> None:
    """Configuring breakaway flags succeeds without error."""
    with WindowsJobObject(breakaway_ok=True, silent_breakaway_ok=True) as job:
        assert job.handle is not None
        assert not job.is_closed
