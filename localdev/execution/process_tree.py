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
import threading
from typing import Any, Self

import psutil

from localdev.schemas import ProcessMemoryMetrics


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
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False


def sample_process_tree_rss(
    root: subprocess.Popen[Any] | psutil.Process | int,
) -> int:
    """Sample the total resident set size (RSS) in bytes of a process and its descendants.

    Aggregates the physical resident memory pages of the root process and all its
    active child and grandchild processes. Safely handles rapid process termination,
    access denial, and zombie process transitions without raising exceptions.

    Args:
        root: Target process identifier (subprocess.Popen, psutil.Process, or PID int).

    Returns:
        Aggregate RSS in bytes across the active process tree, or 0 if unreachable/dead.
    """
    root_pid: int = root if isinstance(root, int) else root.pid

    if root_pid <= 0:
        return 0

    try:
        root_proc = psutil.Process(root_pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return 0

    procs_to_sample: list[psutil.Process] = [root_proc]
    try:
        children = root_proc.children(recursive=True)
        procs_to_sample.extend(children)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        pass

    total_rss = 0
    for proc in procs_to_sample:
        try:
            mem_info = proc.memory_info()
            total_rss += int(mem_info.rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue

    return total_rss


class ProcessTreeMemoryMonitor:
    """Periodic background RSS memory sampler for target process trees.

    Samples the aggregate resident set size (RSS) of the target process and all
    descendants at regular intervals (default: 20 ms / 0.02s) to track peak memory
    consumption. Provides thread-safe metrics with explicit approximation metadata.

    Architectural separation:
    - Measures OS resident physical memory pages of the target process tree.
    - Strictly distinct from Python heap allocations (tracemalloc), Windows Job Object limits,
      and external Ollama service residency.
    """

    def __init__(
        self,
        root: subprocess.Popen[Any] | psutil.Process | int,
        interval_seconds: float = 0.02,
    ) -> None:
        if isinstance(root, (subprocess.Popen, psutil.Process)):
            self.root_pid = root.pid
        elif isinstance(root, int):
            self.root_pid = root
        else:
            raise TypeError(
                f"Expected subprocess.Popen, psutil.Process, or int, got {type(root).__name__}"
            )

        self.interval_seconds = max(0.001, float(interval_seconds))
        self._peak_rss_bytes: int = 0
        self._sample_count: int = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def peak_rss_bytes(self) -> int:
        """Approximate peak RSS in bytes observed during sampling."""
        with self._lock:
            return self._peak_rss_bytes

    @property
    def sample_count(self) -> int:
        """Total number of RSS samples collected."""
        with self._lock:
            return self._sample_count

    def _sample_once(self) -> None:
        """Perform a single thread-safe RSS sample of the process tree."""
        current_rss = sample_process_tree_rss(self.root_pid)
        if current_rss > 0:
            with self._lock:
                self._peak_rss_bytes = max(self._peak_rss_bytes, current_rss)
                self._sample_count += 1
        else:
            with self._lock:
                self._sample_count += 1

    def _run_loop(self) -> None:
        """Periodic background sampling loop."""
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.interval_seconds):
                break
            if not is_process_running(self.root_pid):
                break
            self._sample_once()

    def start(self) -> None:
        """Start the periodic background sampling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        # Take initial sample synchronously while process is fresh and active
        self._sample_once()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"ProcessMemorySampler-{self.root_pid}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the sampling thread to stop, wait for exit, and take a final sample."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if is_process_running(self.root_pid):
            self._sample_once()

    def get_metrics(self) -> ProcessMemoryMetrics:
        """Return structured ProcessMemoryMetrics capturing sampled peak and metadata."""
        with self._lock:
            return ProcessMemoryMetrics(
                approximate_peak_process_tree_rss_bytes=self._peak_rss_bytes,
                sample_count=self._sample_count,
                sample_interval_seconds=self.interval_seconds,
            )

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.stop()



def terminate_process_tree(
    root: subprocess.Popen[Any] | psutil.Process | int,
    graceful_timeout: float = 1.0,
    kill_timeout: float = 1.0,
    close_handles: bool = True,
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
        if close_handles:
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

    # Stage 3: Close handles on Popen instance if requested
    if close_handles:
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
