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

from localdev.errors import MalformedSelectorError, SelectorNotFoundError
from localdev.languages.python.selectors import resolve_callable_from_module
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
        help="Execution mode (load_only for P11-T1).",
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
    }

    # Step 1: Direct file-based module load
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

    # Step 2: Selector resolution on loaded module
    try:
        _callable_obj, qualname, is_method = resolve_callable_from_module(
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
