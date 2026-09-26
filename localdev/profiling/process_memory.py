"""Worker process resident set size (RSS) monitoring (Phase 11 Task P11-T4).

Provides external parent-side resident set size (RSS) sampling of the profiling
worker process tree at regular intervals (default: 20 ms / 0.02 s).

Strict Architectural Separation:
    - Worker Process Tree RSS: Approximate peak operating system physical memory pages
      committed across the worker subprocess and its active descendants.
    - Python Heap Allocations (tracemalloc): Explicitly separated object allocations
      within the Python runtime interpreter.
    - External Service Memory (Ollama): Strictly excluded; external services run
      outside the worker process tree.
    - Windows Job Object Limits: Safety ceiling limits, strictly separated from
      actual observed physical memory footprint.
"""

from __future__ import annotations

import subprocess
from typing import Any

import psutil

from localdev.constants import PROCESS_MEMORY_SAMPLE_INTERVAL_MS
from localdev.execution.process_tree import (
    ProcessTreeMemoryMonitor,
    sample_process_tree_rss,
)


class ProfilingProcessMemoryMonitor(ProcessTreeMemoryMonitor):
    """External process tree RSS monitor tuned for disposable profiling workers."""

    def __init__(
        self,
        root: subprocess.Popen[Any] | psutil.Process | int,
        interval_ms: int = PROCESS_MEMORY_SAMPLE_INTERVAL_MS,
    ) -> None:
        super().__init__(root=root, interval_seconds=interval_ms / 1000.0)
        self.interval_ms = interval_ms


def sample_worker_rss(root: subprocess.Popen[Any] | psutil.Process | int) -> int:
    """Sample the total resident set size (RSS) in bytes of the worker process tree."""
    return sample_process_tree_rss(root)
