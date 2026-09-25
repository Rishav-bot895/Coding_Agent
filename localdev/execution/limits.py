"""Execution limits and timeouts for subprocess management.

In accordance with the Runtime and Isolation Contract:
- Default wall-clock timeout: 10.0 seconds (configurable).
- Default output byte cap: 512 KB combined stdout/stderr (configurable).
- Process limits terminate runaway loops and child processes; they do NOT constitute a security sandbox.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from localdev.constants import DEFAULT_OUTPUT_BYTE_CAP, DEFAULT_TIMEOUT_SECONDS


class ExecutionLimits(BaseModel):
    """Resource limits and timeouts for controlled target subprocess execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        gt=0.0,
        description="Maximum wall-clock execution time in seconds.",
    )
    output_byte_cap: int = Field(
        default=DEFAULT_OUTPUT_BYTE_CAP,
        gt=0,
        description="Maximum combined stdout and stderr byte capture limit.",
    )
    max_process_memory_bytes: int | None = Field(
        default=None,
        gt=0,
        description="Optional maximum process RSS memory limit in bytes.",
    )
    fail_on_job_failure: bool = Field(
        default=False,
        description="Whether to fail closed if Windows Job Object creation or assignment fails.",
    )
    prefer_job_object: bool = Field(
        default=True,
        description="Whether to prefer Windows Job Object for process containment on Windows.",
    )
    sample_interval_seconds: float = Field(
        default=0.02,
        gt=0.0,
        description="Sampling interval in seconds for process tree RSS measurement.",
    )


