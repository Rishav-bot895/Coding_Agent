"""Process tree discovery and reliable descendant termination with psutil.

Provides:
- Recursive discovery of child and grandchild processes.
- Two-stage termination: graceful terminate() followed by forced kill().
- Process exit waiting with timeouts to prevent zombie processes.
- Handle-closed checks on subprocess pipes before returning control.
- Hard guards against terminating the parent process or unrelated siblings.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import psutil


def get_descendant_processes(pid: int) -> list[psutil.Process]:
    """Recursively discover all descendant processes of a given PID.

    Args:
        pid: Process identifier whose children and grandchildren to find.

    Returns:
        List of psutil.Process objects representing active descendant processes.
    """
    if pid <= 0 or pid == os.getpid():
        return []

    try:
        parent = psutil.Process(pid)
        return parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def is_process_running(pid: int) -> bool:
    """Return True if a process with the given PID is currently active.

    Args:
        pid: Process identifier to inspect.

    Returns:
        True if the process exists and is not a zombie.
    """
    if pid <= 0:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def terminate_process_tree(
    root: subprocess.Popen[Any] | psutil.Process | int,
    graceful_timeout: float = 1.0,
    kill_timeout: float = 1.0,
) -> tuple[list[int], list[int]]:
    """Recursively terminate a process and all its child and grandchild processes.

    Executes a two-stage termination sequence:
    1. Sends graceful terminate() to all descendant processes and the root process.
       Waits up to graceful_timeout for processes to exit.
    2. Sends forced kill() to any processes that remain alive after Stage 1.
       Waits up to kill_timeout for forced exit.
    3. Closes any open pipe handles on subprocess.Popen instances.

    Args:
        root: Root process to terminate (subprocess.Popen, psutil.Process, or PID int).
        graceful_timeout: Maximum seconds to wait after terminate().
        kill_timeout: Maximum seconds to wait after kill().

    Returns:
        Tuple of (terminated_pids, surviving_pids).

    Raises:
        ValueError: If root PID matches the current executing process (os.getpid()).
    """
    popen_instance: subprocess.Popen[Any] | None = None
    if isinstance(root, subprocess.Popen):
        popen_instance = root
        root_pid = root.pid
    elif isinstance(root, psutil.Process):
        root_pid = root.pid
    elif isinstance(root, int):
        root_pid = root
    else:
        raise TypeError(f"Expected subprocess.Popen, psutil.Process, or int, got {type(root).__name__}")

    if root_pid <= 0:
        return [], []

    current_pid = os.getpid()
    if root_pid == current_pid:
        raise ValueError(
            f"Refusing to terminate PID {root_pid}: cannot terminate current host process."
        )

    # 1. Discover all descendants recursively
    descendants = get_descendant_processes(root_pid)

    # 2. Collect root process wrapper
    all_procs: list[psutil.Process] = []
    try:
        root_proc = psutil.Process(root_pid)
        all_procs.append(root_proc)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    all_procs.extend(descendants)

    # Filter out current process just in case
    filtered_procs = [p for p in all_procs if p.pid != current_pid]
    if not filtered_procs:
        _cleanup_popen_handles(popen_instance)
        return [], []

    # Stage 1: Graceful termination
    for p in filtered_procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass

    gone, alive = psutil.wait_procs(filtered_procs, timeout=graceful_timeout)

    # Stage 2: Forced kill for any stubborn survivors
    if alive:
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                pass
        gone_kill, alive = psutil.wait_procs(alive, timeout=kill_timeout)
        gone.extend(gone_kill)

    # Stage 3: Close handles on Popen instance
    _cleanup_popen_handles(popen_instance)

    surviving_pids = [p.pid for p in alive if is_process_running(p.pid)]
    all_target_pids = {p.pid for p in filtered_procs}
    terminated_pids = [pid for pid in all_target_pids if pid not in surviving_pids]

    return terminated_pids, surviving_pids


def _cleanup_popen_handles(proc: subprocess.Popen[Any] | None) -> None:
    """Ensure all standard pipe handles on a subprocess.Popen instance are closed."""
    if proc is None:
        return

    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, stream_name, None)
        if stream is not None and not getattr(stream, "closed", True):
            try:
                stream.close()
            except (ValueError, OSError):
                pass

    try:
        proc.wait(timeout=0.1)
    except (subprocess.TimeoutExpired, OSError):
        pass
