"""Windows process-tree memory measurement and reporting tests (P6-T3).

Verifies:
- Periodic RSS sampling of the target process tree during execution.
- Aggregating parent and descendant process RSS into approximate_peak_process_tree_rss_bytes.
- Clear approximation metadata and schema validation.
- Handling of process exit races without unhandled exceptions.
- Strict architectural separation between process RSS, Job Object limits,
  tracemalloc heap allocations, Ollama residency, and total system RAM.
- Terminal and JSON reporting of memory metrics.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import psutil
import pytest
from pydantic import ValidationError

from localdev.cli import main
from localdev.constants import EXIT_SUCCESS
from localdev.execution.limits import ExecutionLimits
from localdev.execution.process_tree import (
    ProcessTreeMemoryMonitor,
    sample_process_tree_rss,
    terminate_process_tree,
)
from localdev.execution.runner import build_execution_request, run_execution_request
from localdev.reporting.terminal import TerminalReporter
from localdev.schemas import ExecutionResult, ProcessMemoryMetrics

pytestmark = [pytest.mark.windows]


def test_sample_process_tree_rss_single_process_known_allocation(tmp_path: Path) -> None:
    """sample_process_tree_rss reflects known memory allocation increase within sampling tolerance."""
    script = tmp_path / "alloc_single.py"
    # Allocate 25 MB and dirty the pages so they reside in physical RSS
    script.write_text(
        textwrap.dedent(
            """
            import sys
            import time

            # Allocate 25 MB
            data = bytearray(25 * 1024 * 1024)
            # Dirty pages across the allocation
            for i in range(0, len(data), 4096):
                data[i] = 1

            sys.stdout.write("ALLOCATED\\n")
            sys.stdout.flush()

            # Wait for signal on stdin to terminate cleanly
            sys.stdin.readline()
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait for the process to allocate memory
        ready_line = proc.stdout.readline() if proc.stdout else ""
        assert "ALLOCATED" in ready_line

        rss_bytes = sample_process_tree_rss(proc)
        # Should reflect at least 25 MB (26,214,400 bytes) plus Python baseline
        assert rss_bytes >= 25 * 1024 * 1024
    finally:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.write("EXIT\n")
                proc.stdin.flush()
            except OSError:
                pass
        proc.kill()
        proc.wait(timeout=2.0)


def test_sample_process_tree_rss_multi_process_tree_aggregation(tmp_path: Path) -> None:
    """sample_process_tree_rss aggregates physical RSS across parent and child processes."""
    script = tmp_path / "alloc_tree.py"
    pid_file = tmp_path / "tree_pids.txt"

    # Parent allocates 20 MB and spawns child which allocates 20 MB
    script.write_text(
        textwrap.dedent(
            f"""
            import os
            import subprocess
            import sys
            import time
            from pathlib import Path

            pid_file = Path(r"{pid_file}")
            is_child = "--child" in sys.argv

            # Allocate 20 MB
            data = bytearray(20 * 1024 * 1024)
            for i in range(0, len(data), 4096):
                data[i] = 1

            with open(pid_file, "a", encoding="utf-8") as f:
                f.write(f"{{'CHILD' if is_child else 'PARENT'}}:{{os.getpid()}}\\n")
                f.flush()

            if not is_child:
                child = subprocess.Popen([sys.executable, __file__, "--child"])
                time.sleep(2.0)
                child.kill()
            else:
                time.sleep(2.0)
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Poll until both parent and child have recorded their PIDs
        deadline = time.perf_counter() + 5.0
        pids: dict[str, int] = {}
        while time.perf_counter() < deadline and len(pids) < 2:
            if pid_file.exists():
                lines = [ln.strip() for ln in pid_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
                for line in lines:
                    role, pid_str = line.split(":", 1)
                    pids[role] = int(pid_str)
            time.sleep(0.05)

        assert "PARENT" in pids and "CHILD" in pids

        # Sample aggregate process tree RSS
        tree_rss = sample_process_tree_rss(proc)
        # Parent alone
        parent_proc = psutil.Process(proc.pid)
        parent_alone_rss = parent_proc.memory_info().rss

        # Combined tree RSS must exceed parent alone and reflect aggregate ~40 MB
        assert tree_rss >= 38 * 1024 * 1024
        assert tree_rss > parent_alone_rss
    finally:
        terminate_process_tree(proc, close_handles=True)


def test_process_tree_memory_monitor_periodic_sampling(tmp_path: Path) -> None:
    """ProcessTreeMemoryMonitor samples periodic background peaks and tracks sample counts."""
    script = tmp_path / "transient_peak.py"
    # Allocates 25 MB, holds for 150ms (capturing multiple 20ms samples), then releases
    script.write_text(
        textwrap.dedent(
            """
            import gc
            import time

            # Initial baseline
            time.sleep(0.05)

            # Transient peak: 25 MB
            data = bytearray(25 * 1024 * 1024)
            for i in range(0, len(data), 4096):
                data[i] = 1
            time.sleep(0.15)

            # Release allocation
            del data
            gc.collect()
            time.sleep(0.05)
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    with ProcessTreeMemoryMonitor(proc, interval_seconds=0.02) as monitor:
        proc.wait(timeout=5.0)

    # Verify peak captured during execution
    assert monitor.peak_rss_bytes >= 25 * 1024 * 1024
    # Verify multiple periodic samples collected
    assert monitor.sample_count >= 3

    metrics = monitor.get_metrics()
    assert isinstance(metrics, ProcessMemoryMetrics)
    assert metrics.approximate_peak_process_tree_rss_bytes == monitor.peak_rss_bytes
    assert metrics.sample_count == monitor.sample_count
    assert metrics.sample_interval_seconds == 0.02
    assert metrics.metric_type == "process_tree_rss"
    assert metrics.is_approximation is True


def test_process_exit_race_safety() -> None:
    """sample_process_tree_rss and ProcessTreeMemoryMonitor handle process exit races without errors."""
    # 1. Invalid / zero / negative PIDs
    assert sample_process_tree_rss(0) == 0
    assert sample_process_tree_rss(-1) == 0
    assert sample_process_tree_rss(9999999) == 0

    # 2. Process exiting mid-sampling
    for _ in range(5):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Immediately sample in rapid succession as process terminates
        monitor = ProcessTreeMemoryMonitor(proc, interval_seconds=0.01)
        monitor.start()
        time.sleep(0.05)
        monitor.stop()

        metrics = monitor.get_metrics()
        assert metrics.sample_count >= 1
        assert metrics.approximate_peak_process_tree_rss_bytes >= 0


def test_runner_populates_memory_metrics(tmp_path: Path) -> None:
    """run_execution_request populates ExecutionResult with accurate ProcessMemoryMetrics."""
    script = tmp_path / "runner_mem.py"
    script.write_text(
        textwrap.dedent(
            """
            import time

            data = bytearray(20 * 1024 * 1024)
            for i in range(0, len(data), 4096):
                data[i] = 1
            time.sleep(0.08)
            print("OK")
            """
        ),
        encoding="utf-8",
    )

    req = build_execution_request(target_path=script)
    limits = ExecutionLimits(sample_interval_seconds=0.01)
    result = run_execution_request(req, limits=limits)

    assert result.exit_code == 0
    assert "OK" in result.stdout

    # Peak RSS fields
    assert result.peak_process_tree_rss_bytes is not None
    assert result.approximate_peak_process_tree_rss_bytes is not None
    assert result.peak_process_tree_rss_bytes == result.approximate_peak_process_tree_rss_bytes
    assert result.approximate_peak_process_tree_rss_bytes >= 20 * 1024 * 1024

    # Detailed metrics
    assert result.memory_metrics is not None
    assert (
        result.memory_metrics.approximate_peak_process_tree_rss_bytes
        == result.approximate_peak_process_tree_rss_bytes
    )
    assert result.memory_metrics.sample_count >= 1
    assert result.memory_metrics.sample_interval_seconds == 0.01
    assert result.memory_metrics.metric_type == "process_tree_rss"
    assert result.memory_metrics.is_approximation is True


def test_architectural_memory_separation_contract() -> None:
    """ProcessMemoryMetrics schema strictly adheres to the 5-way architectural separation."""
    # 1. Verification of default and explicit schema fields
    metrics = ProcessMemoryMetrics(
        approximate_peak_process_tree_rss_bytes=10485760,
        sample_count=5,
        sample_interval_seconds=0.02,
    )
    assert metrics.metric_type == "process_tree_rss"
    assert metrics.is_approximation is True

    # 2. Strict model config (extra forbidden to prevent conflating metrics)
    with pytest.raises(ValidationError):
        ProcessMemoryMetrics(  # type: ignore[call-arg]
            approximate_peak_process_tree_rss_bytes=1000,
            sample_count=1,
            sample_interval_seconds=0.02,
            tracemalloc_bytes=500,  # Must be rejected!
        )

    # 3. Schema documentation explicitly mentions all 5 distinct memory metrics
    doc = ProcessMemoryMetrics.__doc__ or ""
    assert "Target Python process-tree RSS" in doc
    assert "Windows Job Object" in doc
    assert "tracemalloc" in doc
    assert "Ollama" in doc
    assert "system committed RAM" in doc


def test_terminal_and_json_reporting_of_process_memory(tmp_path: Path) -> None:
    """Terminal and JSON reporters display approximate peak process RSS with metadata."""
    script = tmp_path / "report_mem.py"
    script.write_text("print('mem reporting test')\n", encoding="utf-8")

    # 1. TerminalReporter.render_debug
    result = ExecutionResult(
        exit_code=0,
        stdout="mem reporting test\n",
        duration_seconds=0.123,
        peak_process_tree_rss_bytes=15728640,  # 15.00 MB
        execution_backend="windows_job",
        memory_metrics=ProcessMemoryMetrics(
            approximate_peak_process_tree_rss_bytes=15728640,
            sample_count=6,
            sample_interval_seconds=0.02,
        ),
    )
    reporter = TerminalReporter()
    rendered = reporter.render_debug(str(script), result)
    assert "Peak Process RSS:      15.00 MB (approximate, sampled)" in rendered

    # 2. CLI debug --json reporting
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["debug", "--json", str(script)])

    assert code == EXIT_SUCCESS
    envelope = json.loads(stdout.getvalue())
    assert envelope["command"] == "debug"
    assert envelope["success"] is True

    data = envelope["data"]
    assert "approximate_peak_process_tree_rss_bytes" in data
    assert data["approximate_peak_process_tree_rss_bytes"] > 0
    assert "memory_metrics" in data
    assert data["memory_metrics"]["metric_type"] == "process_tree_rss"
    assert data["memory_metrics"]["is_approximation"] is True
    assert data["memory_metrics"]["sample_count"] >= 1

