"""Profiling subsystem for localdev (Phase 11).

Provides disposable worker isolation, target loading via direct file specs,
hot-process function invocation timing, tracemalloc heap allocation tracking,
and parent-side process-tree RSS sampling.
"""

from __future__ import annotations

__all__ = [
    "HOT_PROCESS_SEMANTICS_NOTE",
    "TRACEMALLOC_MEMORY_NOTE",
    "BenchmarkResult",
    "InvocationMemory",
    "InvocationTimer",
    "MemoryStats",
    "ProfileInput",
    "ProfileInputManager",
    "ProfilingProcessMemoryMonitor",
    "TargetImportError",
    "TargetInvocationError",
    "TargetLoadResult",
    "TargetProfileResult",
    "TimingStats",
    "TracemallocTracker",
    "compute_json_depth",
    "compute_memory_stats",
    "compute_timing_stats",
    "load_module_from_path",
    "load_target_in_worker",
    "profile_target_in_worker",
    "run_benchmark",
    "sample_worker_rss",
]

from localdev.errors import TargetInvocationError
from localdev.profiling.benchmark import (
    HOT_PROCESS_SEMANTICS_NOTE,
    TRACEMALLOC_MEMORY_NOTE,
    BenchmarkResult,
    ProfileInput,
    ProfileInputManager,
    compute_json_depth,
    run_benchmark,
)
from localdev.profiling.loader import (
    TargetImportError,
    TargetLoadResult,
    TargetProfileResult,
    load_module_from_path,
    load_target_in_worker,
    profile_target_in_worker,
)
from localdev.profiling.process_memory import (
    ProfilingProcessMemoryMonitor,
    sample_worker_rss,
)
from localdev.profiling.python_memory import (
    InvocationMemory,
    MemoryStats,
    TracemallocTracker,
    compute_memory_stats,
)
from localdev.profiling.timer import (
    InvocationTimer,
    TimingStats,
    compute_timing_stats,
)
