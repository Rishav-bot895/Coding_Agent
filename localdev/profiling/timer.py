"""High-resolution function invocation timer and statistics (Phase 11 Task P11-T3).

Measures function execution duration with high-resolution monotonic clock
(`time.perf_counter_ns`) and computes dispersion statistics (median, min,
max, mean, standard deviation).
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from types import TracebackType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field


class TimingStats(BaseModel):
    """Dispersion statistics for measured invocation durations."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0, description="Total number of measured invocations.")
    median_ns: float = Field(ge=0.0, description="Median invocation duration in nanoseconds.")
    min_ns: int = Field(ge=0, description="Minimum invocation duration in nanoseconds.")
    max_ns: int = Field(ge=0, description="Maximum invocation duration in nanoseconds.")
    mean_ns: float = Field(ge=0.0, description="Mean invocation duration in nanoseconds.")
    stdev_ns: float = Field(ge=0.0, description="Sample standard deviation in nanoseconds.")
    durations_ns: list[int] = Field(
        default_factory=list, description="Durations in nanoseconds for each run."
    )

    @property
    def median_ms(self) -> float:
        """Median duration converted to milliseconds."""
        return self.median_ns / 1_000_000.0

    @property
    def min_ms(self) -> float:
        """Minimum duration converted to milliseconds."""
        return self.min_ns / 1_000_000.0

    @property
    def max_ms(self) -> float:
        """Maximum duration converted to milliseconds."""
        return self.max_ns / 1_000_000.0

    @property
    def mean_ms(self) -> float:
        """Mean duration converted to milliseconds."""
        return self.mean_ns / 1_000_000.0

    @property
    def stdev_ms(self) -> float:
        """Standard deviation converted to milliseconds."""
        return self.stdev_ns / 1_000_000.0

    @property
    def durations_ms(self) -> list[float]:
        """All individual durations converted to milliseconds."""
        return [ns / 1_000_000.0 for ns in self.durations_ns]


def compute_timing_stats(durations_ns: Sequence[int]) -> TimingStats:
    """Compute timing dispersion statistics from a sequence of nanosecond measurements.

    Args:
        durations_ns: Sequence of invocation durations in nanoseconds (must have len >= 1).

    Returns:
        Populated TimingStats instance.

    Raises:
        ValueError: If durations_ns is empty.
    """
    if not durations_ns:
        raise ValueError("Cannot compute timing statistics from an empty duration sequence.")

    count = len(durations_ns)
    median_val = float(statistics.median(durations_ns))
    min_val = min(durations_ns)
    max_val = max(durations_ns)
    mean_val = float(statistics.mean(durations_ns))
    stdev_val = float(statistics.stdev(durations_ns)) if count > 1 else 0.0

    return TimingStats(
        count=count,
        median_ns=median_val,
        min_ns=min_val,
        max_ns=max_val,
        mean_ns=mean_val,
        stdev_ns=stdev_val,
        durations_ns=list(durations_ns),
    )


class InvocationTimer:
    """Context manager for high-resolution monotonic invocation timing."""

    def __init__(self) -> None:
        self._start_ns: int = 0
        self._duration_ns: int = 0

    def __enter__(self) -> Self:
        self._start_ns = time.perf_counter_ns()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._duration_ns = time.perf_counter_ns() - self._start_ns

    @property
    def duration_ns(self) -> int:
        """Invocation duration in nanoseconds."""
        return self._duration_ns

    @property
    def duration_ms(self) -> float:
        """Invocation duration in milliseconds."""
        return self._duration_ns / 1_000_000.0
