"""Python heap allocation tracking via tracemalloc (Phase 11 Task P11-T3).

Measures peak Python object allocations on the Python heap using `tracemalloc`.

Measurement Semantics & Important Distinctions:
    This module tracks Python heap object allocations (`python_allocations_tracemalloc_bytes`)
    instrumented via the Python runtime's tracemalloc module. It measures Python object
    allocations rather than total operating system process memory or resident set size (RSS).
    C extensions, interpreter binaries, memory mappings, page tables, and native allocations
    are tracked separately by external OS process sampling in Phase 11 Task P11-T4.
"""

from __future__ import annotations

import statistics
import tracemalloc
from collections.abc import Sequence
from types import TracebackType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field


class InvocationMemory(BaseModel):
    """Memory measurement for a single invocation tracked by tracemalloc."""

    model_config = ConfigDict(extra="forbid")

    python_allocations_tracemalloc_bytes: int = Field(
        ge=0,
        description="Peak Python heap memory allocated during invocation (in bytes).",
    )
    peak_traced_bytes: int = Field(
        ge=0,
        description="Absolute peak traced memory reached during invocation (in bytes).",
    )
    start_traced_bytes: int = Field(
        ge=0,
        description="Traced memory present at invocation start (in bytes).",
    )
    end_traced_bytes: int = Field(
        ge=0,
        description="Traced memory remaining at invocation completion (in bytes).",
    )
    net_retained_bytes: int = Field(
        description="Net retained memory difference (end_traced_bytes - start_traced_bytes).",
    )


class MemoryStats(BaseModel):
    """Dispersion statistics for measured Python heap allocations."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0, description="Total number of measured invocations.")
    median_bytes: float = Field(ge=0.0, description="Median peak allocation in bytes.")
    min_bytes: int = Field(ge=0, description="Minimum peak allocation in bytes.")
    max_bytes: int = Field(ge=0, description="Maximum peak allocation in bytes.")
    mean_bytes: float = Field(ge=0.0, description="Mean peak allocation in bytes.")
    stdev_bytes: float = Field(
        ge=0.0, description="Sample standard deviation of peak allocations in bytes."
    )
    allocations_bytes: list[int] = Field(
        default_factory=list,
        description="Peak allocation in bytes recorded for each measured run.",
    )


def compute_memory_stats(allocations_bytes: Sequence[int]) -> MemoryStats:
    """Compute memory dispersion statistics from a sequence of byte measurements.

    Args:
        allocations_bytes: Sequence of peak allocation bytes (must have len >= 1).

    Returns:
        Populated MemoryStats instance.

    Raises:
        ValueError: If allocations_bytes is empty.
    """
    if not allocations_bytes:
        raise ValueError("Cannot compute memory statistics from an empty allocation sequence.")

    count = len(allocations_bytes)
    median_val = float(statistics.median(allocations_bytes))
    min_val = min(allocations_bytes)
    max_val = max(allocations_bytes)
    mean_val = float(statistics.mean(allocations_bytes))
    stdev_val = float(statistics.stdev(allocations_bytes)) if count > 1 else 0.0

    return MemoryStats(
        count=count,
        median_bytes=median_val,
        min_bytes=min_val,
        max_bytes=max_val,
        mean_bytes=mean_val,
        stdev_bytes=stdev_val,
        allocations_bytes=list(allocations_bytes),
    )


class TracemallocTracker:
    """Context manager to measure tracemalloc peak allocations for a single invocation.

    Ensures tracemalloc is active, resets the peak memory watermark prior to
    executing the block, and calculates the peak heap allocations tracked
    during that block.
    """

    def __init__(self) -> None:
        self._start_bytes: int = 0
        self._end_bytes: int = 0
        self._peak_bytes: int = 0

    def __enter__(self) -> Self:
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        tracemalloc.reset_peak()
        self._start_bytes, _ = tracemalloc.get_traced_memory()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._end_bytes, self._peak_bytes = tracemalloc.get_traced_memory()

    def result(self) -> InvocationMemory:
        """Obtain the recorded memory measurement."""
        alloc_bytes = max(0, self._peak_bytes - self._start_bytes)
        return InvocationMemory(
            python_allocations_tracemalloc_bytes=alloc_bytes,
            peak_traced_bytes=self._peak_bytes,
            start_traced_bytes=self._start_bytes,
            end_traced_bytes=self._end_bytes,
            net_retained_bytes=self._end_bytes - self._start_bytes,
        )
