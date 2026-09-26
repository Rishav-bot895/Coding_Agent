"""Disposable profiling worker subprocess (Phase 11 Task P11-T1).

Executes as an isolated process under `python -E -B -P`. Directly loads target
modules via `importlib.util.spec_from_file_location`, captures import metrics
and side effects, resolves selectors, and reports structured JSON responses.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from localdev.constants import (
    DEFAULT_MEASURED_INVOCATIONS,
    DEFAULT_WARMUP_INVOCATIONS,
)
from localdev.errors import (
    MalformedSelectorError,
    ProfileInputError,
    SelectorNotFoundError,
    TargetInvocationError,
)
from localdev.languages.python.selectors import resolve_callable_from_module
from localdev.profiling.benchmark import ProfileInputManager, run_benchmark
from localdev.profiling.loader import (
    RESPONSE_SENTINEL,
    TargetImportError,
    load_module_from_path,
)


def create_worker_parser() -> argparse.ArgumentParser:
    """Create command-line argument parser for profiling worker."""
    parser = argparse.ArgumentParser(
        description="Localdev disposable profiling worker subprocess.",
        add_help=True,
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Path to Python target file to load.",
    )
    parser.add_argument(
        "--selector",
        required=True,
        help="Function or method selector string.",
    )
    parser.add_argument(
        "--mode",
        choices=["load_only", "profile"],
        default="load_only",
        help="Execution mode (load_only for P11-T1, profile for P11-T3).",
    )
    parser.add_argument(
        "--input",
        dest="input_file",
        help="Path to JSON file supplying positional/keyword arguments to function.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_INVOCATIONS,
        help="Number of warm-up invocations before measurement.",
    )
    parser.add_argument(
        "--measured",
        type=int,
        default=DEFAULT_MEASURED_INVOCATIONS,
        help="Number of measured invocations to record.",
    )
    parser.add_argument(
        "--response-file",
        help="Path to file where structured JSON response will be written.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for disposable profiling worker."""
    parser = create_worker_parser()
    args = parser.parse_args(argv)

    target_path = Path(args.target).resolve()
    selector = args.selector
    resp_file = Path(args.response_file) if args.response_file else None

    result: dict[str, object] = {
        "target": str(target_path),
        "selector": selector,
        "success": False,
        "import_duration_ms": 0.0,
        "import_stdout": "",
        "import_stderr": "",
        "resolved_name": None,
        "is_method": False,
        "error_type": None,
        "error_message": None,
        "input_valid": False,
        "args_count": 0,
        "kwargs_keys": [],
    }

    # Step 1: Input argument validation (if --input provided)
    if args.input_file:
        try:
            input_mgr = ProfileInputManager.from_file(args.input_file)
            result["input_valid"] = True
            result["args_count"] = len(input_mgr.args)
            result["kwargs_keys"] = list(input_mgr.kwargs.keys())
        except ProfileInputError as pie:
            result["error_type"] = "ProfileInputError"
            result["error_message"] = str(pie)
            _write_response(result, resp_file)
            return 0
        except (OSError, UnicodeDecodeError) as exc:
            result["error_type"] = type(exc).__name__
            result["error_message"] = f"Failed to read input file: {exc}"
            _write_response(result, resp_file)
            return 0
    else:
        input_mgr = ProfileInputManager.empty()
        result["input_valid"] = True
        result["args_count"] = 0
        result["kwargs_keys"] = []

    # Step 2: Direct file-based module load
    try:
        module, import_duration_ms, import_stdout, import_stderr = load_module_from_path(
            target_path, module_name="profile_target"
        )
        result["import_duration_ms"] = import_duration_ms
        result["import_stdout"] = import_stdout
        result["import_stderr"] = import_stderr
    except TargetImportError as tie:
        result["import_duration_ms"] = tie.duration_ms
        result["import_stdout"] = tie.stdout
        result["import_stderr"] = tie.stderr
        orig = tie.original_exception
        result["error_type"] = type(orig).__name__ if orig else "ImportError"
        result["error_message"] = str(tie)
        _write_response(result, resp_file)
        return 0
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
        _write_response(result, resp_file)
        return 0

    # Step 3: Selector resolution on loaded module
    callable_obj = None
    try:
        callable_obj, qualname, is_method = resolve_callable_from_module(
            module=module,
            selector=selector,
            target_path=str(target_path),
        )
        result["success"] = True
        result["resolved_name"] = qualname
        result["is_method"] = is_method
    except (MalformedSelectorError, SelectorNotFoundError) as se:
        result["error_type"] = type(se).__name__
        result["error_message"] = str(se)
    except (AttributeError, TypeError, ValueError) as exc:
        result["error_type"] = type(exc).__name__
        result["error_message"] = f"Unexpected resolution error: {exc}"

    # Step 4: Hot-process benchmark execution (if mode == "profile" and resolution succeeded)
    if args.mode == "profile" and result["success"] and callable_obj is not None:
        try:
            bench_res = run_benchmark(
                target_callable=callable_obj,
                input_manager=input_mgr,
                warmup_runs=args.warmup,
                measured_runs=args.measured,
            )
            result["warmup_runs"] = bench_res.warmup_runs
            result["measured_runs"] = bench_res.measured_runs
            result["warmup_durations_ns"] = bench_res.warmup_durations_ns
            result["warmup_allocations_bytes"] = bench_res.warmup_allocations_bytes
            result["timing"] = bench_res.timing.model_dump()
            result["memory"] = bench_res.memory.model_dump()
            result["hot_process_semantics"] = bench_res.hot_process_semantics
            result["semantics_note"] = bench_res.semantics_note
            result["memory_note"] = bench_res.memory_note
        except TargetInvocationError as tie:
            result["success"] = False
            result["error_type"] = type(tie).__name__
            result["error_message"] = str(tie)
        except Exception as exc:  # noqa: BLE001 - worker subprocess boundary isolates target failures
            result["success"] = False
            result["error_type"] = type(exc).__name__
            result["error_message"] = f"Profiling failed unexpectedly: {exc}"

    _write_response(result, resp_file)
    return 0


def _write_response(result: dict[str, object], resp_file: Path | None) -> None:
    """Write structured JSON response to file and stdout."""
    payload = json.dumps(result, indent=2)

    if resp_file is not None:
        try:
            resp_file.parent.mkdir(parents=True, exist_ok=True)
            resp_file.write_text(payload, encoding="utf-8")
        except OSError:
            pass

    sys.stdout.write(f"\n{RESPONSE_SENTINEL}\n{payload}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
