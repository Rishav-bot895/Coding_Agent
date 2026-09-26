"""Profiling input validation, argument management, and mutation protection (Phase 11 Task P11-T2).

Provides schema validation, depth checking, size limiting, and deep re-creation
of invocation arguments for function profiling without intra-benchmark mutation
contamination.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from localdev.constants import (
    DEFAULT_MEASURED_INVOCATIONS,
    DEFAULT_WARMUP_INVOCATIONS,
    MAX_PROFILE_INPUT_BYTES,
    MAX_PROFILE_INPUT_DEPTH,
)
from localdev.errors import ProfileInputError, TargetInvocationError
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


def compute_json_depth(value: Any, current_depth: int = 1) -> int:
    """Compute the maximum nesting depth of a parsed JSON structure.

    Args:
        value: Parsed JSON object, array, or primitive.
        current_depth: Current recursion depth (starts at 1).

    Returns:
        Maximum nesting depth (integer >= 1).
    """
    if isinstance(value, dict):
        if not value:
            return current_depth
        return max(compute_json_depth(v, current_depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        if not value:
            return current_depth
        return max(compute_json_depth(item, current_depth + 1) for item in value)
    return current_depth


class ProfileInput(BaseModel):
    """Strongly-typed schema for profiling function inputs."""

    model_config = ConfigDict(extra="forbid")

    args: list[Any] = Field(
        default_factory=list,
        description="Positional arguments passed to the target function.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments passed to the target function.",
    )

    @field_validator("kwargs")
    @classmethod
    def validate_kwarg_names(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Ensure all kwarg keys are valid Python identifiers."""
        for k in v:
            if not isinstance(k, str) or not k.isidentifier():
                raise ValueError(
                    f"Invalid keyword argument '{k}': must be a valid Python identifier."
                )
        return v


class ProfileInputManager:
    """Validates profiling input JSON and provides fresh argument copies for every invocation."""

    def __init__(self, raw_json_str: str) -> None:
        self._raw_json_str = raw_json_str
        self._input_model = self._parse_and_validate(raw_json_str)

    @classmethod
    def from_file(cls, path: Path | str) -> ProfileInputManager:
        """Load and validate profiling inputs from a JSON file.

        Args:
            path: Path to JSON input file.

        Returns:
            Validated ProfileInputManager instance.

        Raises:
            ProfileInputError: If file is missing, oversized, malformed, or fails validation.
        """
        input_path = Path(path).resolve()
        if not input_path.is_file():
            raise ProfileInputError(f"Profiling input file does not exist: {input_path}")

        try:
            size = input_path.stat().st_size
        except OSError as exc:
            raise ProfileInputError(f"Failed to inspect input file: {exc}") from exc

        if size > MAX_PROFILE_INPUT_BYTES:
            raise ProfileInputError(
                f"Profiling input file size ({size} bytes) exceeds maximum allowed limit of 1 MB "
                f"({MAX_PROFILE_INPUT_BYTES} bytes)."
            )

        try:
            content = input_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ProfileInputError(f"Profiling input file must be UTF-8 encoded: {exc}") from exc
        except OSError as exc:
            raise ProfileInputError(f"Failed to read input file: {exc}") from exc

        return cls(content)

    @classmethod
    def from_json(cls, raw_json: str) -> ProfileInputManager:
        """Validate profiling inputs from a raw JSON string."""
        return cls(raw_json)

    @classmethod
    def empty(cls) -> ProfileInputManager:
        """Create an empty ProfileInputManager with no args and no kwargs."""
        return cls("{}")

    def _parse_and_validate(self, text: str) -> ProfileInput:
        """Parse raw JSON string, check limits, and validate schema."""
        trimmed = text.strip()
        if not trimmed:
            raise ProfileInputError("Profiling input JSON cannot be empty or whitespace.")

        if len(text.encode("utf-8")) > MAX_PROFILE_INPUT_BYTES:
            raise ProfileInputError(
                f"Profiling input payload exceeds maximum allowed size of 1 MB ({MAX_PROFILE_INPUT_BYTES} bytes)."
            )

        try:
            data = json.loads(trimmed)
        except json.JSONDecodeError as exc:
            raise ProfileInputError(f"Malformed JSON in profiling input: {exc}") from exc

        if not isinstance(data, dict):
            raise ProfileInputError(
                "Profiling input top-level value must be a JSON object ({...}) with optional 'args' and 'kwargs'."
            )

        # Enforce maximum nesting depth
        depth = compute_json_depth(data)
        if depth > MAX_PROFILE_INPUT_DEPTH:
            raise ProfileInputError(
                f"Profiling input JSON nesting depth ({depth}) exceeds maximum limit of "
                f"{MAX_PROFILE_INPUT_DEPTH} levels."
            )

        try:
            return ProfileInput.model_validate(data)
        except Exception as exc:
            raise ProfileInputError(f"Profiling input schema validation failed: {exc}") from exc

    @property
    def model(self) -> ProfileInput:
        """Return the underlying ProfileInput model."""
        return self._input_model

    @property
    def args(self) -> list[Any]:
        """Return a fresh copy of the validated positional arguments."""
        return copy.deepcopy(self._input_model.args)

    @property
    def kwargs(self) -> dict[str, Any]:
        """Return a fresh copy of the validated keyword arguments."""
        return copy.deepcopy(self._input_model.kwargs)

    @property
    def raw_json(self) -> str:
        """Return the raw JSON string."""
        return self._raw_json_str

    def recreate_arguments(self) -> tuple[list[Any], dict[str, Any]]:
        """Reconstruct fresh, unshared deep copies of args and kwargs for an invocation.

        Guarantees that arguments mutated by a function (e.g. list.sort(), dict.pop())
        do not leak across invocations.

        Returns:
            Tuple of (fresh_args, fresh_kwargs).
        """
        return copy.deepcopy(self._input_model.args), copy.deepcopy(self._input_model.kwargs)


HOT_PROCESS_SEMANTICS_NOTE: Final[str] = (
    "Profiling executes within a hot worker process where the target module is imported once. "
    "Arguments are recreated freshly for each run to prevent argument mutation, but module-level state "
    "(global variables, caches, lru_cache, singletons) persists across invocations. "
    "This measures repeated invocation performance rather than fresh-process startup costs."
)

TRACEMALLOC_MEMORY_NOTE: Final[str] = (
    "Measures Python heap object allocations tracked by tracemalloc, not total OS process memory."
)


class BenchmarkResult(BaseModel):
    """Dispersion statistics and execution metadata for a hot-process benchmark."""

    model_config = ConfigDict(extra="forbid")

    warmup_runs: int = Field(ge=0, description="Count of warm-up invocations performed.")
    measured_runs: int = Field(ge=1, description="Count of measured invocations performed.")
    warmup_durations_ns: list[int] = Field(
        default_factory=list, description="Durations of warm-up runs in nanoseconds."
    )
    warmup_allocations_bytes: list[int] = Field(
        default_factory=list,
        description="Peak tracemalloc allocations for warm-up runs in bytes.",
    )
    timing: TimingStats = Field(description="Timing dispersion statistics across measured runs.")
    memory: MemoryStats = Field(description="Memory dispersion statistics across measured runs.")
    hot_process_semantics: bool = Field(
        default=True,
        description="Whether benchmarking was executed under hot-process semantics.",
    )
    semantics_note: str = Field(
        default=HOT_PROCESS_SEMANTICS_NOTE,
        description="Explicit note regarding hot-process semantics and persistent state.",
    )
    memory_note: str = Field(
        default=TRACEMALLOC_MEMORY_NOTE,
        description="Explicit note distinguishing tracemalloc heap allocations from OS memory.",
    )


def run_benchmark(
    target_callable: Callable[..., Any],
    input_manager: ProfileInputManager,
    warmup_runs: int = DEFAULT_WARMUP_INVOCATIONS,
    measured_runs: int = DEFAULT_MEASURED_INVOCATIONS,
) -> BenchmarkResult:
    """Execute warm-up and measured invocations under hot-process semantics.

    Reconstructs fresh, unmutated arguments for each invocation. Records duration
    via high-resolution monotonic timer and peak heap allocations via tracemalloc.

    Args:
        target_callable: Resolved callable object to profile.
        input_manager: ProfileInputManager providing deep-recreated arguments.
        warmup_runs: Number of unmeasured warm-up invocations (default: 2).
        measured_runs: Number of measured invocations (default: 7, must be >= 1).

    Returns:
        BenchmarkResult with computed dispersion statistics and notes.

    Raises:
        ValueError: If measured_runs < 1 or warmup_runs < 0.
        TargetInvocationError: If target_callable raises an exception during execution.
    """
    if warmup_runs < 0:
        raise ValueError(f"warmup_runs cannot be negative: {warmup_runs}")
    if measured_runs < 1:
        raise ValueError(f"measured_runs must be at least 1: {measured_runs}")

    warmup_durations: list[int] = []
    warmup_allocations: list[int] = []

    # Warm-up phase
    for i in range(warmup_runs):
        fresh_args, fresh_kwargs = input_manager.recreate_arguments()
        timer = InvocationTimer()
        tracker = TracemallocTracker()
        try:
            with timer, tracker:
                target_callable(*fresh_args, **fresh_kwargs)
        except Exception as exc:
            raise TargetInvocationError(
                f"Exception during warm-up invocation {i + 1} of {warmup_runs}: {type(exc).__name__}: {exc}",
                original_exception_type=type(exc).__name__,
                invocation_index=i,
                is_warmup=True,
            ) from exc

        warmup_durations.append(timer.duration_ns)
        warmup_allocations.append(tracker.result().python_allocations_tracemalloc_bytes)

    # Measured phase
    measured_durations: list[int] = []
    measured_allocations: list[int] = []

    for i in range(measured_runs):
        fresh_args, fresh_kwargs = input_manager.recreate_arguments()
        timer = InvocationTimer()
        tracker = TracemallocTracker()
        try:
            with timer, tracker:
                target_callable(*fresh_args, **fresh_kwargs)
        except Exception as exc:
            raise TargetInvocationError(
                f"Exception during measured invocation {i + 1} of {measured_runs}: {type(exc).__name__}: {exc}",
                original_exception_type=type(exc).__name__,
                invocation_index=i,
                is_warmup=False,
            ) from exc

        mem_res = tracker.result()
        measured_durations.append(timer.duration_ns)
        measured_allocations.append(mem_res.python_allocations_tracemalloc_bytes)

    timing_stats = compute_timing_stats(measured_durations)
    memory_stats = compute_memory_stats(measured_allocations)

    return BenchmarkResult(
        warmup_runs=warmup_runs,
        measured_runs=measured_runs,
        warmup_durations_ns=warmup_durations,
        warmup_allocations_bytes=warmup_allocations,
        timing=timing_stats,
        memory=memory_stats,
    )
