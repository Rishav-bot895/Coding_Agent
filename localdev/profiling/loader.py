"""Target module loading and disposable worker execution (Phase 11 Task P11-T1).

Directly loads Python modules from their file path using importlib without
modifying sys.path or polluting project imports. Spawns disposable worker
subprocesses under Windows Job Object containment and controlled limits.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import time
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from localdev.execution.limits import ExecutionLimits
from localdev.execution.runner import build_execution_request, run_execution_request

WORKER_SCRIPT_PATH = Path(__file__).parent / "worker.py"
RESPONSE_SENTINEL = "__LOCALDEV_WORKER_RESPONSE__"


class TargetImportError(Exception):
    """Exception raised when loading a target module directly from file path fails."""

    def __init__(
        self,
        message: str,
        duration_ms: float,
        stdout: str,
        stderr: str,
        original_exception: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.duration_ms = duration_ms
        self.stdout = stdout
        self.stderr = stderr
        self.original_exception = original_exception


class TargetLoadResult(BaseModel):
    """Structured report of loading a target module in a disposable worker subprocess."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="Target file path.")
    selector: str = Field(description="Function or method selector string.")
    success: bool = Field(description="Whether the target loaded and resolved successfully.")
    import_duration_ms: float = Field(ge=0.0, default=0.0, description="Time to load module in ms.")
    import_stdout: str = Field(default="", description="Captured stdout emitted during import.")
    import_stderr: str = Field(default="", description="Captured stderr emitted during import.")
    resolved_name: str | None = Field(
        default=None, description="Resolved qualified function/method name."
    )
    is_method: bool = Field(default=False, description="Whether resolved target is a class method.")
    error_type: str | None = Field(default=None, description="Error class name if load failed.")
    error_message: str | None = Field(default=None, description="Detailed error explanation.")
    timed_out: bool = Field(default=False, description="Whether execution timed out.")
    exit_code: int = Field(default=0, description="Worker subprocess exit code.")


def load_module_from_path(
    target_path: Path | str,
    module_name: str = "profile_target",
) -> tuple[types.ModuleType, float, str, str]:
    """Load a Python module directly from its file path using importlib.

    Enforces direct file-based loading without modifying sys.path to include
    the target directory. Captures import duration, stdout, and stderr.

    Args:
        target_path: Path to Python source file.
        module_name: Module name to register in sys.modules during execution.

    Returns:
        Tuple of (loaded_module, import_duration_ms, import_stdout, import_stderr).

    Raises:
        FileNotFoundError: If target file does not exist.
        ImportError: If module spec cannot be created.
        TargetImportError: If an exception occurs while executing the module body.
    """
    path = Path(target_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Target file does not exist: {path}")

    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for target: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    t0 = time.perf_counter()

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            spec.loader.exec_module(module)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return module, duration_ms, stdout_buf.getvalue(), stderr_buf.getvalue()
    except BaseException as exc:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        sys.modules.pop(module_name, None)
        raise TargetImportError(
            message=f"{type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            original_exception=exc,
        ) from exc


def load_target_in_worker(
    target_path: Path | str,
    selector: str,
    timeout: float = 10.0,
    python_executable: str | Path | None = None,
    fail_on_job_failure: bool = False,
) -> TargetLoadResult:
    """Execute a disposable worker subprocess to load target and resolve selector.

    Args:
        target_path: Path to Python target file.
        selector: Function or method selector string.
        timeout: Subprocess timeout in seconds.
        python_executable: Python interpreter binary path.
        fail_on_job_failure: Strict mode for Windows Job Object assignment.

    Returns:
        TargetLoadResult capturing import metrics and resolution status.
    """
    resolved_target = str(Path(target_path).resolve())

    with tempfile.TemporaryDirectory(prefix="localdev_prof_") as tmp_dir:
        resp_file = Path(tmp_dir) / "worker_response.json"

        worker_args = [
            "--target",
            resolved_target,
            "--selector",
            selector,
            "--mode",
            "load_only",
            "--response-file",
            str(resp_file),
        ]

        exec_req = build_execution_request(
            target_path=WORKER_SCRIPT_PATH,
            args=worker_args,
            python_executable=python_executable,
        )

        limits = ExecutionLimits(
            timeout_seconds=timeout,
            fail_on_job_failure=fail_on_job_failure,
        )

        exec_res = run_execution_request(exec_req, limits=limits, timeout=timeout)

        if exec_res.timed_out:
            return TargetLoadResult(
                target=resolved_target,
                selector=selector,
                success=False,
                timed_out=True,
                exit_code=exec_res.exit_code,
                error_type="TimeoutError",
                error_message=f"Target import or worker execution timed out after {timeout:.1f} seconds.",
                import_stdout=exec_res.stdout,
                import_stderr=exec_res.stderr,
            )

        # Check response file first
        if resp_file.is_file():
            try:
                data = json.loads(resp_file.read_text(encoding="utf-8"))
                return TargetLoadResult(
                    target=data.get("target", resolved_target),
                    selector=data.get("selector", selector),
                    success=data.get("success", False),
                    import_duration_ms=data.get("import_duration_ms", 0.0),
                    import_stdout=data.get("import_stdout", ""),
                    import_stderr=data.get("import_stderr", ""),
                    resolved_name=data.get("resolved_name"),
                    is_method=data.get("is_method", False),
                    error_type=data.get("error_type"),
                    error_message=data.get("error_message"),
                    timed_out=False,
                    exit_code=exec_res.exit_code,
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                return TargetLoadResult(
                    target=resolved_target,
                    selector=selector,
                    success=False,
                    exit_code=exec_res.exit_code,
                    error_type="WorkerProtocolError",
                    error_message=f"Failed to parse worker response file: {exc}",
                    import_stdout=exec_res.stdout,
                    import_stderr=exec_res.stderr,
                )

        # Fallback: check stdout for sentinel
        if RESPONSE_SENTINEL in exec_res.stdout:
            try:
                _, json_part = exec_res.stdout.split(RESPONSE_SENTINEL, 1)
                data = json.loads(json_part.strip())
                return TargetLoadResult(
                    target=data.get("target", resolved_target),
                    selector=data.get("selector", selector),
                    success=data.get("success", False),
                    import_duration_ms=data.get("import_duration_ms", 0.0),
                    import_stdout=data.get("import_stdout", ""),
                    import_stderr=data.get("import_stderr", ""),
                    resolved_name=data.get("resolved_name"),
                    is_method=data.get("is_method", False),
                    error_type=data.get("error_type"),
                    error_message=data.get("error_message"),
                    timed_out=False,
                    exit_code=exec_res.exit_code,
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass

        # Worker crashed or exited without producing valid response
        err_msg = exec_res.stderr.strip() or f"Worker process crashed with exit code {exec_res.exit_code}."
        return TargetLoadResult(
            target=resolved_target,
            selector=selector,
            success=False,
            exit_code=exec_res.exit_code,
            error_type="WorkerCrashError",
            error_message=err_msg,
            import_stdout=exec_res.stdout,
            import_stderr=exec_res.stderr,
        )
