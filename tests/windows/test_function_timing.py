"""Windows disposable worker function timing and memory profiling tests (Phase 11 Task P11-T3).

Verifies:
1. High-resolution monotonic timing (perf_counter_ns) and dispersion statistics (median, min, max, mean, stdev).
2. Tracemalloc-based Python heap allocation tracking and dispersion statistics.
3. Hot-process semantics: target module loaded once; warm-ups and measured runs executed
   in same environment with fresh argument re-creation.
4. Hot-process state persistence: global variables, caches, @lru_cache persist across runs.
5. Error handling: function exceptions and execution timeouts caught and reported in structured results.
6. Documentation notes: explicit disclaimers separating tracemalloc heap allocations from OS RSS,
   and hot-process repeated invocation performance from fresh startup costs.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from localdev.errors import TargetInvocationError
from localdev.profiling.benchmark import (
    HOT_PROCESS_SEMANTICS_NOTE,
    TRACEMALLOC_MEMORY_NOTE,
    BenchmarkResult,
    ProfileInputManager,
    run_benchmark,
)
from localdev.profiling.loader import profile_target_in_worker
from localdev.profiling.python_memory import (
    MemoryStats,
    TracemallocTracker,
    compute_memory_stats,
)
from localdev.profiling.timer import (
    InvocationTimer,
    TimingStats,
    compute_timing_stats,
)

pytestmark = pytest.mark.windows

TARGETS_DIR = Path("tests/profiling_samples/targets")


# =============================================================================
# Unit Tests: Timing Measurement & Dispersion Statistics
# =============================================================================


def test_invocation_timer_measures_elapsed_time() -> None:
    """Verify InvocationTimer accurately measures elapsed execution time."""
    timer = InvocationTimer()
    with timer:
        time.sleep(0.010)

    assert timer.duration_ns > 0
    assert timer.duration_ms >= 5.0
    assert timer.duration_ms < 500.0


def test_compute_timing_stats_valid() -> None:
    """Verify dispersion stats computed across nanosecond measurements."""
    durations = [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000]
    stats = compute_timing_stats(durations)

    assert stats.count == 5
    assert stats.min_ns == 1_000_000
    assert stats.max_ns == 5_000_000
    assert stats.median_ns == 3_000_000.0
    assert stats.mean_ns == 3_000_000.0
    assert stats.stdev_ns > 0.0

    # Test millisecond conversions
    assert stats.min_ms == 1.0
    assert stats.max_ms == 5.0
    assert stats.median_ms == 3.0
    assert stats.mean_ms == 3.0
    assert len(stats.durations_ms) == 5
    assert stats.durations_ms[0] == 1.0


def test_compute_timing_stats_single_measurement() -> None:
    """Verify standard deviation is 0.0 when only 1 measurement is provided."""
    stats = compute_timing_stats([42_000_000])
    assert stats.count == 1
    assert stats.min_ns == 42_000_000
    assert stats.max_ns == 42_000_000
    assert stats.median_ns == 42_000_000.0
    assert stats.stdev_ns == 0.0
    assert stats.stdev_ms == 0.0


def test_compute_timing_stats_empty_raises() -> None:
    """Verify empty sequence raises ValueError."""
    with pytest.raises(ValueError, match="empty duration sequence"):
        compute_timing_stats([])


# =============================================================================
# Unit Tests: Tracemalloc Python Memory Tracking
# =============================================================================


def test_tracemalloc_tracker_records_allocations() -> None:
    """Verify TracemallocTracker records heap allocation bytes during a block."""
    tracker = TracemallocTracker()
    with tracker:
        data = bytearray(1_000_000)
        assert len(data) == 1_000_000

    mem_res = tracker.result()
    assert mem_res.python_allocations_tracemalloc_bytes >= 1_000_000
    assert mem_res.peak_traced_bytes >= mem_res.start_traced_bytes


def test_compute_memory_stats_valid() -> None:
    """Verify memory dispersion stats computed across allocation byte measurements."""
    allocations = [1000, 2000, 3000, 4000, 5000]
    stats = compute_memory_stats(allocations)

    assert stats.count == 5
    assert stats.min_bytes == 1000
    assert stats.max_bytes == 5000
    assert stats.median_bytes == 3000.0
    assert stats.mean_bytes == 3000.0
    assert stats.stdev_bytes > 0.0
    assert stats.allocations_bytes == allocations


def test_compute_memory_stats_single_measurement() -> None:
    """Verify memory stdev is 0.0 when only 1 measurement is provided."""
    stats = compute_memory_stats([1024])
    assert stats.count == 1
    assert stats.min_bytes == 1024
    assert stats.max_bytes == 1024
    assert stats.stdev_bytes == 0.0


def test_compute_memory_stats_empty_raises() -> None:
    """Verify empty allocation sequence raises ValueError."""
    with pytest.raises(ValueError, match="empty allocation sequence"):
        compute_memory_stats([])


# =============================================================================
# Unit Tests: In-Process Benchmark Runner
# =============================================================================


def test_run_benchmark_pure_function() -> None:
    """Verify run_benchmark executes warmups and measured runs cleanly."""
    mgr = ProfileInputManager('{"args": [10, 20]}')

    def add(x: int, y: int) -> int:
        return x + y

    res: BenchmarkResult = run_benchmark(add, mgr, warmup_runs=2, measured_runs=5)

    assert res.warmup_runs == 2
    assert res.measured_runs == 5
    assert len(res.warmup_durations_ns) == 2
    assert len(res.warmup_allocations_bytes) == 2
    assert res.timing.count == 5
    assert res.memory.count == 5
    assert res.hot_process_semantics is True
    assert HOT_PROCESS_SEMANTICS_NOTE in res.semantics_note
    assert TRACEMALLOC_MEMORY_NOTE in res.memory_note


def test_run_benchmark_validation() -> None:
    """Verify parameter validation in run_benchmark."""
    mgr = ProfileInputManager.empty()

    def dummy() -> None:
        pass

    with pytest.raises(ValueError, match="warmup_runs cannot be negative"):
        run_benchmark(dummy, mgr, warmup_runs=-1, measured_runs=5)

    with pytest.raises(ValueError, match="measured_runs must be at least 1"):
        run_benchmark(dummy, mgr, warmup_runs=2, measured_runs=0)


def test_run_benchmark_target_exception() -> None:
    """Verify TargetInvocationError raised when function fails in run_benchmark."""
    mgr = ProfileInputManager.empty()

    def failing() -> None:
        raise ArithmeticError("boom")

    with pytest.raises(TargetInvocationError, match="Exception during warm-up invocation 1"):
        run_benchmark(failing, mgr, warmup_runs=1, measured_runs=5)


# =============================================================================
# Subprocess Worker Integration Tests (profile_target_in_worker)
# =============================================================================


def test_worker_profiling_timing_sleep() -> None:
    """Verify worker subprocess profiling on a controlled 15ms sleep target."""
    target_path = TARGETS_DIR / "timing_samples.py"

    result = profile_target_in_worker(
        target_path=target_path,
        selector="sleep_fifteen_ms",
        warmup_runs=2,
        measured_runs=5,
    )

    assert result.success is True, f"Worker failed: {result.error_message}"
    assert result.resolved_name == "sleep_fifteen_ms"
    assert result.warmup_runs == 2
    assert result.measured_runs == 5
    assert isinstance(result.timing, TimingStats)

    # Windows timer granularity: 15ms sleep typically executes in 14-20ms
    assert result.timing.count == 5
    assert result.timing.median_ms >= 10.0
    assert result.timing.min_ms <= result.timing.median_ms <= result.timing.max_ms
    assert result.hot_process_semantics is True


def test_worker_profiling_pure_computation() -> None:
    """Verify worker subprocess profiling on a computation target."""
    target_path = TARGETS_DIR / "timing_samples.py"

    result = profile_target_in_worker(
        target_path=target_path,
        selector="compute_squares",
        warmup_runs=2,
        measured_runs=7,
    )

    assert result.success is True, f"Worker failed: {result.error_message}"
    assert result.timing is not None
    assert result.timing.count == 7
    assert result.timing.median_ms >= 0.0
    assert result.timing.stdev_ms >= 0.0
    assert result.memory is not None
    assert result.memory.count == 7


def test_worker_profiling_stateful_hot_process_persistence(tmp_path: Path) -> None:
    """Verify module-level state persists across warmups and measured runs in hot worker."""
    target_path = TARGETS_DIR / "stateful_samples.py"

    # Input passes a new number each call
    input_file = tmp_path / "stateful_in.json"
    input_file.write_text('{"args": [99]}', encoding="utf-8")

    result = profile_target_in_worker(
        target_path=target_path,
        selector="record_history",
        input_file=input_file,
        warmup_runs=2,
        measured_runs=5,
    )

    assert result.success is True, f"Worker failed: {result.error_message}"
    assert result.hot_process_semantics is True
    assert result.semantics_note is not None
    assert "hot worker process" in result.semantics_note
    assert "persists across invocations" in result.semantics_note
    assert "repeated invocation performance" in result.semantics_note
    assert result.timing is not None
    assert result.timing.count == 5


def test_worker_profiling_lru_cache_persistence(tmp_path: Path) -> None:
    """Verify @lru_cache persists across hot-process invocations."""
    target_path = TARGETS_DIR / "stateful_samples.py"

    input_file = tmp_path / "lru_in.json"
    input_file.write_text('{"args": [2, 1000]}', encoding="utf-8")

    result = profile_target_in_worker(
        target_path=target_path,
        selector="cached_heavy_computation",
        input_file=input_file,
        warmup_runs=2,
        measured_runs=5,
    )

    assert result.success is True, f"Worker failed: {result.error_message}"
    assert result.timing is not None
    assert result.timing.count == 5
    # Measured runs should hit the hot cache and execute virtually instantaneously
    assert result.timing.median_ms < 5.0


def test_worker_profiling_tracemalloc_allocations() -> None:
    """Verify tracemalloc tracks Python heap allocations accurately and scales predictably."""
    target_path = TARGETS_DIR / "memory_samples.py"

    # 1. Profile 2 MB allocation
    result_2mb = profile_target_in_worker(
        target_path=target_path,
        selector="allocate_two_mb",
        warmup_runs=1,
        measured_runs=3,
    )
    assert result_2mb.success is True, f"Worker failed: {result_2mb.error_message}"
    assert isinstance(result_2mb.memory, MemoryStats)
    assert result_2mb.memory.median_bytes >= 2 * 1024 * 1024
    assert result_2mb.memory_note is not None
    assert "Python heap object allocations tracked by tracemalloc" in result_2mb.memory_note

    # 2. Profile 5 MB allocation
    result_5mb = profile_target_in_worker(
        target_path=target_path,
        selector="allocate_five_mb",
        warmup_runs=1,
        measured_runs=3,
    )
    assert result_5mb.success is True, f"Worker failed: {result_5mb.error_message}"
    assert result_5mb.memory is not None
    assert result_5mb.memory.median_bytes >= 5 * 1024 * 1024

    # 3. Predictable scaling: 5 MB allocation must register higher than 2 MB
    assert result_5mb.memory.median_bytes > result_2mb.memory.median_bytes


def test_worker_profiling_function_exception_handling() -> None:
    """Verify function exceptions during invocation are caught and structured in report."""
    target_path = TARGETS_DIR / "failing_samples.py"

    result = profile_target_in_worker(
        target_path=target_path,
        selector="raise_zero_division",
        warmup_runs=2,
        measured_runs=5,
    )

    assert result.success is False
    assert result.error_type == "TargetInvocationError"
    assert result.error_message is not None
    assert "ZeroDivisionError" in result.error_message
    assert result.timing is None


def test_worker_profiling_timeout_handling(tmp_path: Path) -> None:
    """Verify execution timeout is enforced and caught by parent runner."""
    target_path = tmp_path / "infinite_loop.py"
    target_path.write_text(
        "import time\n"
        "def stall() -> None:\n"
        "    while True:\n"
        "        time.sleep(0.1)\n",
        encoding="utf-8",
    )

    result = profile_target_in_worker(
        target_path=target_path,
        selector="stall",
        timeout=1.0,
        warmup_runs=1,
        measured_runs=1,
    )

    assert result.success is False
    assert result.timed_out is True
    assert result.error_type == "TimeoutError"
    assert "timed out after 1.0 seconds" in (result.error_message or "")


def test_worker_profiling_custom_runs(tmp_path: Path) -> None:
    """Verify custom warmup and measured invocation counts are respected."""
    target_path = TARGETS_DIR / "timing_samples.py"
    input_file = tmp_path / "add_input.json"
    input_file.write_text('{"args": [10, 20]}', encoding="utf-8")

    result = profile_target_in_worker(
        target_path=target_path,
        selector="quick_add",
        input_file=input_file,
        warmup_runs=3,
        measured_runs=4,
    )

    assert result.success is True, f"Worker failed: {result.error_message}"
    assert result.warmup_runs == 3
    assert result.measured_runs == 4
    assert result.timing is not None
    assert result.timing.count == 4
    assert len(result.warmup_durations_ns) == 3
    assert len(result.warmup_allocations_bytes) == 3
