"""Controlled subprocess execution requests and runner for localdev.

Constructs subprocess requests using argument lists, shell=False, the selected
Python executable, flags -E, -B, -P, user invocation directory as default cwd,
and the environment variable allowlist defined in the Runtime and Isolation Contract.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import IO, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from localdev.execution.environment import build_clean_environment
from localdev.execution.limits import ExecutionLimits
from localdev.execution.output_capture import OutputCollector, PipeDrainer
from localdev.execution.process_tree import terminate_process_tree
from localdev.schemas import ExecutionResult


class ExecutionRequest(BaseModel):
    """Subprocess execution request constructed for controlled target execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: list[str] = Field(
        min_length=1,
        description="Command-line argument vector to execute directly via CreateProcessW.",
    )
    cwd: str = Field(
        description="Working directory for subprocess execution (defaults to invocation dir).",
    )
    env: dict[str, str] = Field(
        description="Sanitized environment dictionary containing only allowlisted variables.",
    )
    stdin_data: str | None = Field(
        default=None,
        description="Optional string payload piped to subprocess standard input.",
    )
    stdin_file: Path | None = Field(
        default=None,
        description="Optional file path whose content is redirected to subprocess standard input.",
    )
    shell: Literal[False] = Field(
        default=False,
        description="Must always be False. Target execution via shell is strictly prohibited.",
    )

    @model_validator(mode="after")
    def validate_request(self) -> ExecutionRequest:
        """Validate mutually exclusive options and hard security invariants."""
        if self.stdin_data is not None and self.stdin_file is not None:
            raise ValueError("Cannot specify both 'stdin_data' and 'stdin_file'.")
        if self.shell is not False:
            raise ValueError("shell=True is strictly prohibited for target execution.")
        return self


def build_execution_request(
    target_path: str | Path,
    args: Sequence[str] | None = None,
    python_executable: str | Path | None = None,
    cwd: str | Path | None = None,
    env_overrides: Mapping[str, str] | None = None,
    stdin_data: str | None = None,
    stdin_file: str | Path | None = None,
    base_env: Mapping[str, str] | None = None,
) -> ExecutionRequest:
    """Build a deterministic, isolated subprocess execution request.

    Args:
        target_path: Path to the target script (e.g. relocated session copy or target file).
        args: Optional list of additional command-line arguments passed to the script.
        python_executable: Python interpreter binary to use. Defaults to sys.executable.
        cwd: Subprocess working directory. Defaults to invocation working directory (Path.cwd()).
        env_overrides: Optional environment overrides (PYTHON* variables prohibited).
        stdin_data: String data to feed via standard input.
        stdin_file: Path to file whose content will be fed via standard input.
        base_env: Base environment to filter. Defaults to os.environ.

    Returns:
        A validated ExecutionRequest instance.
    """
    py_exec = str(Path(python_executable or sys.executable).resolve())
    resolved_target = str(Path(target_path).resolve())

    # Invariant: [python, "-E", "-B", "-P", target, *args]
    cmd = [
        py_exec,
        "-E",
        "-B",
        "-P",
        resolved_target,
        *[str(a) for a in (args or [])],
    ]

    resolved_cwd = str(Path(cwd).resolve()) if cwd is not None else str(Path.cwd().resolve())
    clean_env = build_clean_environment(env_overrides=env_overrides, base_env=base_env)

    resolved_stdin_file = Path(stdin_file).resolve() if stdin_file is not None else None

    return ExecutionRequest(
        command=cmd,
        cwd=resolved_cwd,
        env=clean_env,
        stdin_data=stdin_data,
        stdin_file=resolved_stdin_file,
        shell=False,
    )


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    """Terminate a subprocess and its entire descendant process tree."""
    terminate_process_tree(proc, graceful_timeout=0.5, kill_timeout=0.5)


def run_execution_request(
    request: ExecutionRequest,
    limits: ExecutionLimits | None = None,
    timeout: float | None = None,
) -> ExecutionResult:
    """Execute the target subprocess request under controlled limits.

    Concurrently drains stdout and stderr pipes into a byte-capped OutputCollector
    to prevent pipe buffer deadlocks and memory exhaustion. Enforces wall-clock timeout
    and output byte limits, terminating the subprocess on breach and capturing partial output.

    Args:
        request: Prepared ExecutionRequest to execute.
        limits: Optional ExecutionLimits specifying timeout and byte caps.
        timeout: Optional wall-clock timeout override in seconds.

    Returns:
        Structured ExecutionResult with exit code, outputs, elapsed time,
        and limit breach indicators (timed_out, output_truncated).
    """
    effective_limits = limits or ExecutionLimits()
    timeout_seconds = timeout if timeout is not None else effective_limits.timeout_seconds
    output_byte_cap = effective_limits.output_byte_cap

    collector = OutputCollector(byte_cap=output_byte_cap)
    input_bytes: bytes | None = None
    start_time = time.perf_counter()
    timed_out = False
    output_truncated = False

    stdin_source: int | IO[bytes] | None = None

    with ExitStack() as stack:
        if request.stdin_file is not None:
            stdin_source = stack.enter_context(open(request.stdin_file, "rb"))
        elif request.stdin_data is not None:
            input_bytes = request.stdin_data.encode("utf-8")
            stdin_source = subprocess.PIPE
        else:
            stdin_source = subprocess.PIPE

        proc = subprocess.Popen(
            request.command,
            cwd=request.cwd,
            env=request.env,
            stdin=stdin_source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )

        drainer = PipeDrainer(
            stdout_stream=proc.stdout,
            stderr_stream=proc.stderr,
            collector=collector,
            stdin_stream=proc.stdin if input_bytes is not None else None,
            stdin_data=input_bytes,
        )
        drainer.start()

        while True:
            if proc.poll() is not None:
                break

            if collector.cap_reached_event.is_set():
                output_truncated = True
                _terminate_process(proc)
                break

            elapsed = time.perf_counter() - start_time
            if elapsed >= timeout_seconds:
                timed_out = True
                _terminate_process(proc)
                break

            collector.cap_reached_event.wait(timeout=0.01)

        # Allow drainer threads to complete residual data reading
        drainer.wait(timeout=0.5)

        # Ensure stdin pipe handle is closed
        if proc.stdin is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except (ValueError, OSError):
                pass

        # Ensure process has fully exited
        if proc.poll() is None:
            _terminate_process(proc)

        returncode = proc.returncode if proc.returncode is not None else -1

    duration = time.perf_counter() - start_time
    if collector.is_truncated:
        output_truncated = True

    return ExecutionResult(
        exit_code=returncode,
        stdout=collector.get_stdout_text(),
        stderr=collector.get_stderr_text(),
        duration_seconds=duration,
        timed_out=timed_out,
        output_truncated=output_truncated,
    )
