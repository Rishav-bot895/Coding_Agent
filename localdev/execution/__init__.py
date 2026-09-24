"""Execution module providing subprocess request construction, isolation, limits, and process trees."""

from __future__ import annotations

from localdev.execution.environment import build_clean_environment, is_allowed_env_var
from localdev.execution.limits import ExecutionLimits
from localdev.execution.output_capture import OutputCollector, PipeDrainer
from localdev.execution.process_tree import (
    get_descendant_processes,
    is_process_running,
    terminate_process_tree,
)
from localdev.execution.runner import (
    ExecutionRequest,
    build_execution_request,
    run_execution_request,
)
from localdev.execution.windows_job import (
    WindowsJobObject,
    can_create_job_object,
)

__all__ = [
    "ExecutionLimits",
    "ExecutionRequest",
    "OutputCollector",
    "PipeDrainer",
    "WindowsJobObject",
    "build_clean_environment",
    "build_execution_request",
    "can_create_job_object",
    "get_descendant_processes",
    "is_allowed_env_var",
    "is_process_running",
    "run_execution_request",
    "terminate_process_tree",
]

