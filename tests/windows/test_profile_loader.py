"""Windows disposable worker target loading tests (Phase 11 Task P11-T1).

Verifies:
1. Direct file-based target loading in a disposable worker subprocess (-E -B -P).
2. Module function selector and class method selector resolution.
3. Separation of import accounting (duration, stdout, stderr).
4. Error handling: missing selector, ambiguous selector, nested function selector.
5. Import exceptions, slow imports, and timeouts.
6. Worker crash / exit resilience without parent destabilization.
7. Isolation invariants: parent sys.path and sys.modules remain untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from localdev.profiling.loader import (
    TargetImportError,
    load_module_from_path,
    load_target_in_worker,
)

pytestmark = pytest.mark.windows


# =============================================================================
# Direct In-Process Loader Tests (load_module_from_path)
# =============================================================================


def test_load_module_from_path_success(tmp_path: Path) -> None:
    """Verify load_module_from_path loads target module and measures duration."""
    target = tmp_path / "math_sample.py"
    target.write_text(
        "def multiply(a: int, b: int) -> int:\n    return a * b\n",
        encoding="utf-8",
    )

    module, duration_ms, stdout, stderr = load_module_from_path(target, module_name="test_math")
    assert hasattr(module, "multiply")
    assert module.multiply(4, 5) == 20
    assert duration_ms >= 0.0
    assert stdout == ""
    assert stderr == ""


def test_load_module_from_path_captures_import_stdout_stderr(tmp_path: Path) -> None:
    """Verify stdout and stderr emitted during import are captured and separated."""
    target = tmp_path / "noisy_import.py"
    target.write_text(
        "import sys\n"
        "print('stdout: module loading')\n"
        "sys.stderr.write('stderr: module warning\\n')\n"
        "def ready() -> bool:\n    return True\n",
        encoding="utf-8",
    )

    module, _duration_ms, stdout, stderr = load_module_from_path(target, module_name="test_noisy")
    assert "stdout: module loading" in stdout
    assert "stderr: module warning" in stderr
    assert hasattr(module, "ready")


def test_load_module_from_path_import_exception(tmp_path: Path) -> None:
    """Verify import exception raises TargetImportError with duration and captured output."""
    target = tmp_path / "broken_import.py"
    target.write_text(
        "print('pre-crash stdout')\n"
        "raise ValueError('Custom import failure')\n",
        encoding="utf-8",
    )

    with pytest.raises(TargetImportError) as exc_info:
        load_module_from_path(target, module_name="test_broken")

    err = exc_info.value
    assert "ValueError: Custom import failure" in str(err)
    assert "pre-crash stdout" in err.stdout
    assert err.duration_ms >= 0.0
    assert isinstance(err.original_exception, ValueError)


def test_load_module_from_path_non_existent_file() -> None:
    """Verify loading non-existent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_module_from_path("non_existent_file_path_12345.py")


# =============================================================================
# Worker Subprocess Loading Tests (load_target_in_worker)
# =============================================================================


def test_worker_load_module_function(tmp_path: Path) -> None:
    """Verify worker subprocess loads target function and reports clean success."""
    target = tmp_path / "service.py"
    target.write_text(
        "def calculate_tax(amount: float) -> float:\n    return amount * 0.15\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="calculate_tax")
    assert res.success is True
    assert res.resolved_name == "calculate_tax"
    assert res.is_method is False
    assert res.import_duration_ms >= 0.0
    assert res.error_type is None
    assert res.exit_code == 0


def test_worker_load_class_method(tmp_path: Path) -> None:
    """Verify worker subprocess loads target class method and reports is_method=True."""
    target = tmp_path / "calc_service.py"
    target.write_text(
        "class OrderCalculator:\n"
        "    def compute_total(self, subtotal: float) -> float:\n"
        "        return subtotal + 5.0\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="OrderCalculator.compute_total")
    assert res.success is True
    assert res.resolved_name == "OrderCalculator.compute_total"
    assert res.is_method is True
    assert res.error_type is None
    assert res.exit_code == 0


def test_worker_load_missing_selector(tmp_path: Path) -> None:
    """Verify missing selector in worker reports SelectorNotFoundError cleanly."""
    target = tmp_path / "simple.py"
    target.write_text("def ping() -> str:\n    return 'pong'\n", encoding="utf-8")

    res = load_target_in_worker(target, selector="non_existent_func")
    assert res.success is False
    assert res.error_type == "SelectorNotFoundError"
    assert "not found in target module" in (res.error_message or "")


def test_worker_load_ambiguous_selector(tmp_path: Path) -> None:
    """Verify ambiguous selector matching multiple classes reports MalformedSelectorError."""
    target = tmp_path / "ambiguous.py"
    target.write_text(
        "class BackendA:\n"
        "    def execute(self) -> str:\n        return 'A'\n\n"
        "class BackendB:\n"
        "    def execute(self) -> str:\n        return 'B'\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="execute")
    assert res.success is False
    assert res.error_type == "MalformedSelectorError"
    assert "Ambiguous selector 'execute'" in (res.error_message or "")


def test_worker_load_nested_function_rejected(tmp_path: Path) -> None:
    """Verify nested function selector reports MalformedSelectorError."""
    target = tmp_path / "nested.py"
    target.write_text(
        "def outer():\n"
        "    def inner():\n        return 42\n"
        "    return inner()\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="outer.inner")
    assert res.success is False
    assert res.error_type == "MalformedSelectorError"
    assert "nested functions are not supported" in (res.error_message or "")


def test_worker_load_captures_import_side_effects(tmp_path: Path) -> None:
    """Verify worker cleanly separates and reports import-time stdout and stderr."""
    target = tmp_path / "noisy_target.py"
    target.write_text(
        "import sys\n"
        "print('Worker import stdout line')\n"
        "sys.stderr.write('Worker import stderr warning\\n')\n"
        "def run() -> int:\n    return 100\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="run")
    assert res.success is True
    assert "Worker import stdout line" in res.import_stdout
    assert "Worker import stderr warning" in res.import_stderr


def test_worker_load_slow_import(tmp_path: Path) -> None:
    """Verify worker measures slow module import duration accurately."""
    target = tmp_path / "slow_import.py"
    target.write_text(
        "import time\n"
        "time.sleep(0.06)\n"
        "def fast_func() -> int:\n    return 1\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="fast_func")
    assert res.success is True
    # Duration must reflect at least 50 ms
    assert res.import_duration_ms >= 50.0


def test_worker_load_import_exception(tmp_path: Path) -> None:
    """Verify target raising exception at import time is handled gracefully by worker."""
    target = tmp_path / "crashed_import.py"
    target.write_text(
        "import sys\n"
        "print('stdout before import error')\n"
        "raise RuntimeError('Fatal module initialization failure')\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="any_func")
    assert res.success is False
    assert res.error_type == "RuntimeError"
    assert "Fatal module initialization failure" in (res.error_message or "")
    assert "stdout before import error" in res.import_stdout
    assert res.import_duration_ms >= 0.0


def test_worker_load_timeout(tmp_path: Path) -> None:
    """Verify worker timeout on infinite loop at import time kills process tree safely."""
    target = tmp_path / "infinite_import.py"
    target.write_text(
        "import time\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )

    # Use short timeout (0.5s)
    res = load_target_in_worker(target, selector="func", timeout=0.5)
    assert res.success is False
    assert res.timed_out is True
    assert res.error_type == "TimeoutError"


def test_worker_load_crash_resilience(tmp_path: Path) -> None:
    """Verify worker crash (e.g. os._exit) does not destabilize parent CLI."""
    target = tmp_path / "hard_exit.py"
    target.write_text(
        "import os\n"
        "os._exit(42)\n",
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="func")
    assert res.success is False
    assert res.exit_code == 42
    assert res.error_type == "WorkerCrashError"


def test_worker_load_parent_isolation_invariants(tmp_path: Path) -> None:
    """Verify parent sys.path and sys.modules remain strictly unpolluted."""
    target_dir = tmp_path / "user_project_dir"
    target_dir.mkdir()
    target = target_dir / "isolated_script.py"
    target.write_text("def unique_func_name_999(): pass\n", encoding="utf-8")

    pre_paths = list(sys.path)
    pre_modules = set(sys.modules.keys())

    res = load_target_in_worker(target, selector="unique_func_name_999")
    assert res.success is True

    # Assert parent sys.path is completely untouched
    assert sys.path == pre_paths
    assert str(target_dir) not in sys.path

    # Assert parent sys.modules does not contain profile_target or isolated_script
    assert set(sys.modules.keys()) == pre_modules
    assert "profile_target" not in sys.modules
    assert "isolated_script" not in sys.modules
