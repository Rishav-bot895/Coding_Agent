"""Windows process tree discovery and descendant termination tests.

Verifies:
- Recursive discovery of child and grandchild processes.
- Two-stage termination (graceful terminate + forced kill) cleaning up all descendants.
- Process tree termination during runner timeout.
- Unrelated parent and sibling processes remaining untouched.
- Pipe handle closure and prevention of zombie processes.
- Hard safety guards against terminating current host process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from localdev.execution.limits import ExecutionLimits
from localdev.execution.process_tree import (
    get_descendant_processes,
    is_process_running,
    terminate_process_tree,
)
from localdev.execution.runner import build_execution_request, run_execution_request

pytestmark = [pytest.mark.windows]

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "boundary_samples" / "processes"


def _wait_for_pids(pid_file: Path, expected_count: int, timeout: float = 5.0) -> list[int]:
    """Poll a PID recording file until expected number of PIDs are present."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if pid_file.exists():
            lines = [line.strip() for line in pid_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(lines) >= expected_count:
                pids: list[int] = []
                for line in lines:
                    _, pid_str = line.split(":", 1)
                    pids.append(int(pid_str))
                return pids
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {expected_count} PIDs in {pid_file}")


def test_terminate_process_tree_child_and_grandchild(tmp_path: Path) -> None:
    """terminate_process_tree terminates root, child, and grandchild processes."""
    pid_file = tmp_path / "tree_pids.txt"
    script = FIXTURES_DIR / "spawn_tree.py"

    proc = subprocess.Popen(
        [sys.executable, str(script), "--pid-file", str(pid_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        pids = _wait_for_pids(pid_file, expected_count=3, timeout=5.0)
        assert len(pids) == 3

        # Verify all 3 processes are active
        for pid in pids:
            assert is_process_running(pid) is True

        # Terminate entire process tree
        terminated, surviving = terminate_process_tree(proc, graceful_timeout=1.0, kill_timeout=1.0)

        assert surviving == []
        for pid in pids:
            assert pid in terminated

        # Verify none of the PIDs are running
        time.sleep(0.1)
        for pid in pids:
            assert is_process_running(pid) is False

    finally:
        # Emergency cleanup if test failed before terminate
        if proc.poll() is None:
            proc.kill()


def test_runner_timeout_terminates_entire_descendant_tree(tmp_path: Path) -> None:
    """Subprocess execution timeout terminates child and grandchild processes."""
    pid_file = tmp_path / "runner_tree_pids.txt"
    script = FIXTURES_DIR / "spawn_tree.py"

    req = build_execution_request(
        target_path=script,
        args=["--pid-file", str(pid_file)],
    )
    limits = ExecutionLimits(timeout_seconds=1.2)

    result = run_execution_request(req, limits=limits)

    assert result.timed_out is True

    # Read captured PIDs
    assert pid_file.exists()
    pids = _wait_for_pids(pid_file, expected_count=2, timeout=2.0)
    assert len(pids) >= 2

    # Verify all captured descendant processes were terminated
    time.sleep(0.1)
    for pid in pids:
        assert is_process_running(pid) is False


def test_unrelated_sibling_and_parent_processes_not_terminated(tmp_path: Path) -> None:
    """Terminating a process tree leaves unrelated sibling and parent processes alive."""
    # Start an innocent unrelated bystander process
    bystander = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    bystander_pid = bystander.pid

    pid_file = tmp_path / "target_tree.txt"
    script = FIXTURES_DIR / "spawn_child.py"

    target_proc = subprocess.Popen(
        [sys.executable, str(script), "--pid-file", str(pid_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        target_pids = _wait_for_pids(pid_file, expected_count=2, timeout=5.0)

        # Ensure bystander and target processes are running
        assert is_process_running(bystander_pid) is True
        for pid in target_pids:
            assert is_process_running(pid) is True

        # Terminate target tree
        terminated, surviving = terminate_process_tree(target_proc)

        # Target processes must be terminated
        assert surviving == []
        for pid in target_pids:
            assert pid in terminated
            assert is_process_running(pid) is False

        # Host process and bystander must be unaffected
        assert is_process_running(os.getpid()) is True
        assert is_process_running(bystander_pid) is True

    finally:
        bystander.kill()
        bystander.wait()


def test_refuse_to_terminate_current_process() -> None:
    """Attempting to terminate the host process fails closed with ValueError."""
    with pytest.raises(ValueError, match="cannot terminate current host process"):
        terminate_process_tree(os.getpid())

    # Non-positive PIDs return empty lists safely
    assert terminate_process_tree(0) == ([], [])
    assert terminate_process_tree(-1) == ([], [])


def test_already_dead_process() -> None:
    """Terminating an already dead process handles NoSuchProcess gracefully."""
    dead_proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
    )
    dead_proc.wait(timeout=2.0)
    assert dead_proc.poll() is not None

    _terminated, surviving = terminate_process_tree(dead_proc)
    assert surviving == []


def test_get_descendant_processes_boundaries() -> None:
    """get_descendant_processes handles invalid and non-existent PIDs safely."""
    assert get_descendant_processes(0) == []
    assert get_descendant_processes(-1) == []
    assert get_descendant_processes(os.getpid()) == [] or isinstance(
        get_descendant_processes(os.getpid()), list
    )
    # Very high PID that does not exist
    assert get_descendant_processes(9999999) == []


def test_pipe_handles_closed_after_termination() -> None:
    """terminate_process_tree ensures standard pipe handles on Popen are closed."""
    script = FIXTURES_DIR / "spawn_child.py"
    pid_file = Path(os.environ["TEMP"]) / f"pid_pipe_test_{os.getpid()}.txt"

    proc = subprocess.Popen(
        [sys.executable, str(script), "--pid-file", str(pid_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        terminate_process_tree(proc)

        assert proc.stdin.closed is True
        assert proc.stdout.closed is True
        assert proc.stderr.closed is True
    finally:
        if pid_file.exists():
            try:
                pid_file.unlink()
            except OSError:
                pass
