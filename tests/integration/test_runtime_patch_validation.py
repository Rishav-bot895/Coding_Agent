"""Integration tests for candidate runtime patch validation (Validation Levels B and C).

Verifies Phase 9 Task P9-T2 requirements:
1. Executing only the session candidate copy; original source file bytes remain completely unchanged.
2. Level B (Failure reproduction removed): original runtime failure is no longer reproduced,
   even if execution terminates with a different non-zero exit code or different error.
3. Explicit user-facing interpretation: Level B does NOT imply behavioral correctness or bug fix.
4. Level C (Clean execution): candidate exits successfully under controlled runtime (exit code 0).
5. Regression detection: candidate triggers a new unhandled exception or wall-clock timeout.
6. Candidate execution under identical inputs (target_args, stdin_file, timeout, limits).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from localdev.agent.orchestrator import Orchestrator
from localdev.languages.python.traceback_parser import is_same_error_signature
from localdev.schemas import (
    ErrorSignature,
    TargetRecord,
    ValidationLevel,
    ValidationReport,
)


def _create_target(tmp_path: Path, filename: str, content: str) -> tuple[Path, TargetRecord]:
    """Helper to create a target file on disk and return its Path and TargetRecord."""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    raw = p.read_bytes()
    record = TargetRecord(
        path=filename,
        absolute_path=str(p.resolve()),
        file_size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        encoding="utf-8",
        has_bom=False,
        newline_style="\n",
        has_trailing_newline=content.endswith("\n"),
    )
    return p, record


class TestRuntimePatchValidation:
    """Test candidate patch validation across Validation Levels B and C."""

    def test_candidate_clean_execution_certifies_level_c(self, tmp_path: Path) -> None:
        """Original exception is removed and candidate exits with code 0 (Level C pass)."""
        buggy_code = """\
def compute(x):
    return 10 // x

if __name__ == "__main__":
    compute(0)
"""
        target_path, target = _create_target(tmp_path, "divide.py", buggy_code)
        initial_bytes = target_path.read_bytes()
        initial_sha256 = target.sha256

        orchestrator = Orchestrator()
        baseline_exec = orchestrator.debug(target=target)
        assert baseline_exec.exit_code != 0
        assert baseline_exec.error_signature is not None
        assert baseline_exec.error_signature.exception_type == "ZeroDivisionError"

        # Candidate fixes the bug: compute(2)
        candidate_code = """\
def compute(x):
    return 10 // x

if __name__ == "__main__":
    compute(2)
"""
        cand_path = tmp_path / "candidate_clean.py"
        cand_path.write_text(candidate_code, encoding="utf-8")

        report: ValidationReport = orchestrator.validate_candidate(
            target=target,
            candidate_path=cand_path,
            baseline_result=baseline_exec,
        )

        assert report.static_valid is True
        assert report.failure_reproduction_removed is True
        assert report.clean_execution is True
        assert report.level_achieved == ValidationLevel.LEVEL_C
        assert report.baseline_error_signature is not None
        assert report.baseline_error_signature.exception_type == "ZeroDivisionError"
        assert report.candidate_error_signature is None
        assert "clean" in report.details.get("runtime_status", "").lower()

        # Assert original source file bytes remain strictly unchanged
        assert target_path.read_bytes() == initial_bytes
        assert hashlib.sha256(target_path.read_bytes()).hexdigest() == initial_sha256

    def test_original_exception_replaced_by_another_certifies_level_b_not_c(
        self, tmp_path: Path
    ) -> None:
        """Original IndexError is replaced by TypeError: satisfies Level B, fails Level C.

        Demonstrates that Level B does NOT mean the bug is proven fixed.
        """
        buggy_code = """\
def get_item():
    items = [1, 2]
    return items[10]

if __name__ == "__main__":
    get_item()
"""
        target_path, target = _create_target(tmp_path, "index_bug.py", buggy_code)
        initial_sha256 = target.sha256

        orchestrator = Orchestrator()
        baseline_exec = orchestrator.debug(target=target)
        assert baseline_exec.exit_code != 0
        assert baseline_exec.error_signature is not None
        assert baseline_exec.error_signature.exception_type == "IndexError"

        # Candidate replaces IndexError with a TypeError
        flawed_fix_code = """\
def get_item():
    items = [1, 2]
    return items + 5

if __name__ == "__main__":
    get_item()
"""
        cand_path = tmp_path / "candidate_type_error.py"
        cand_path.write_text(flawed_fix_code, encoding="utf-8")

        report: ValidationReport = orchestrator.validate_candidate(
            target=target,
            candidate_path=cand_path,
            baseline_result=baseline_exec,
        )

        assert report.static_valid is True
        assert report.failure_reproduction_removed is True  # Original IndexError removed
        assert report.clean_execution is False  # But execution still crashed
        assert report.level_achieved == ValidationLevel.LEVEL_B
        assert report.baseline_error_signature is not None
        assert report.baseline_error_signature.exception_type == "IndexError"
        assert report.candidate_error_signature is not None
        assert report.candidate_error_signature.exception_type == "TypeError"

        # Explicit user-facing interpretation: Level B does NOT prove the bug is fixed
        status_msg = str(report.details.get("runtime_status", ""))
        assert "Level B does NOT prove the bug is fixed" in status_msg

        # Original source file bytes remain unchanged
        assert hashlib.sha256(target_path.read_bytes()).hexdigest() == initial_sha256

    def test_original_exception_still_reproduced_fails_level_b(self, tmp_path: Path) -> None:
        """Candidate still raises the identical unhandled exception: fails Level B."""
        buggy_code = """\
def divide():
    return 10 // 0

if __name__ == "__main__":
    divide()
"""
        _target_path, target = _create_target(tmp_path, "same_error.py", buggy_code)

        orchestrator = Orchestrator()
        baseline_exec = orchestrator.debug(target=target)
        assert baseline_exec.error_signature is not None
        assert baseline_exec.error_signature.exception_type == "ZeroDivisionError"

        # Ineffective candidate: comment added, but divide() still raises ZeroDivisionError
        candidate_code = """\
def divide():
    # Attempting calculation
    return 10 // 0

if __name__ == "__main__":
    divide()
"""
        cand_path = tmp_path / "candidate_ineffective.py"
        cand_path.write_text(candidate_code, encoding="utf-8")

        report: ValidationReport = orchestrator.validate_candidate(
            target=target,
            candidate_path=cand_path,
            baseline_result=baseline_exec,
        )

        assert report.static_valid is True
        assert report.failure_reproduction_removed is False
        assert report.clean_execution is False
        assert report.level_achieved == ValidationLevel.LEVEL_A
        assert report.candidate_error_signature is not None
        assert report.candidate_error_signature.exception_type == "ZeroDivisionError"
        assert "reproduced" in str(report.details.get("runtime_status", "")).lower()

    def test_candidate_timeout_infinite_loop_fails_validation(self, tmp_path: Path) -> None:
        """Candidate introducing an infinite loop times out, failing Level B & Level C."""
        buggy_code = """\
def solve():
    return 1 // 0

if __name__ == "__main__":
    solve()
"""
        _target_path, target = _create_target(tmp_path, "timeout_target.py", buggy_code)

        orchestrator = Orchestrator()
        baseline_exec = orchestrator.debug(target=target)

        # Candidate introduces an infinite loop
        infinite_loop_code = """\
def solve():
    while True:
        pass

if __name__ == "__main__":
    solve()
"""
        cand_path = tmp_path / "candidate_infinite.py"
        cand_path.write_text(infinite_loop_code, encoding="utf-8")

        report: ValidationReport = orchestrator.validate_candidate(
            target=target,
            candidate_path=cand_path,
            baseline_result=baseline_exec,
            timeout=0.3,  # Fast timeout for test
        )

        assert report.static_valid is True
        assert report.clean_execution is False
        assert report.failure_reproduction_removed is False
        assert report.level_achieved == ValidationLevel.LEVEL_A
        assert report.details.get("candidate_timed_out") is True
        assert "regression" in str(report.details.get("runtime_status", "")).lower()

    def test_candidate_crashes_on_clean_baseline_fails_validation(self, tmp_path: Path) -> None:
        """Candidate introducing a crash on an initially healthy script fails Level B & C."""
        clean_code = """\
def greet():
    print("hello world")

if __name__ == "__main__":
    greet()
"""
        _target_path, target = _create_target(tmp_path, "clean_script.py", clean_code)

        orchestrator = Orchestrator()
        baseline_exec = orchestrator.debug(target=target)
        assert baseline_exec.exit_code == 0
        assert baseline_exec.error_signature is None

        # Regression candidate that crashes
        broken_candidate_code = """\
def greet():
    raise RuntimeError("Newly introduced bug!")

if __name__ == "__main__":
    greet()
"""
        cand_path = tmp_path / "candidate_broken.py"
        cand_path.write_text(broken_candidate_code, encoding="utf-8")

        report: ValidationReport = orchestrator.validate_candidate(
            target=target,
            candidate_path=cand_path,
            baseline_result=baseline_exec,
        )

        assert report.static_valid is True
        assert report.clean_execution is False
        assert report.failure_reproduction_removed is False
        assert report.level_achieved == ValidationLevel.LEVEL_A
        assert report.candidate_error_signature is not None
        assert report.candidate_error_signature.exception_type == "RuntimeError"
        assert "regression" in str(report.details.get("runtime_status", "")).lower()

    def test_candidate_executes_with_target_args_and_stdin(self, tmp_path: Path) -> None:
        """Candidate receives identical target arguments and standard input as baseline."""
        echo_script = """\
import sys

val = sys.stdin.read().strip()
prefix = sys.argv[1] if len(sys.argv) > 1 else ""
if not prefix:
    raise ValueError("Missing required prefix argument")

print(f"{prefix}: {val}")
"""
        _target_path, target = _create_target(tmp_path, "echo_script.py", echo_script)

        stdin_file = tmp_path / "input.txt"
        stdin_file.write_text("agent_payload", encoding="utf-8")

        orchestrator = Orchestrator()

        # 1. Baseline with missing args crashes
        baseline_fail = orchestrator.debug(
            target=target,
            target_args=[],
            stdin_file=stdin_file,
        )
        assert baseline_fail.exit_code != 0
        assert baseline_fail.error_signature is not None
        assert baseline_fail.error_signature.exception_type == "ValueError"

        # 2. Candidate that provides default fallback prefix
        candidate_code = """\
import sys

val = sys.stdin.read().strip()
prefix = sys.argv[1] if len(sys.argv) > 1 else "DEFAULT"
print(f"{prefix}: {val}")
"""
        cand_path = tmp_path / "candidate_fallback.py"
        cand_path.write_text(candidate_code, encoding="utf-8")

        report: ValidationReport = orchestrator.validate_candidate(
            target=target,
            candidate_path=cand_path,
            baseline_result=baseline_fail,
            target_args=[],  # Identical arguments (empty)
            stdin_file=stdin_file,  # Identical stdin input
        )

        assert report.static_valid is True
        assert report.failure_reproduction_removed is True
        assert report.clean_execution is True
        assert report.level_achieved == ValidationLevel.LEVEL_C
        assert "DEFAULT: agent_payload" in str(report.details.get("candidate_stdout", ""))

    def test_original_file_bytes_remain_untouched_throughout(self, tmp_path: Path) -> None:
        """Strict verification that original target file bytes are never modified by validation."""
        original_code = "print('original untouched code')\n"
        target_path, target = _create_target(tmp_path, "guard.py", original_code)
        initial_sha256 = target.sha256

        candidate_code = "print('candidate modified code')\n"
        cand_path = tmp_path / "candidate_guard.py"
        cand_path.write_text(candidate_code, encoding="utf-8")

        orchestrator = Orchestrator()
        val_report = orchestrator.validate_candidate(
            target=target,
            candidate_path=cand_path,
        )

        assert val_report.clean_execution is True
        assert target_path.read_text(encoding="utf-8") == original_code
        assert hashlib.sha256(target_path.read_bytes()).hexdigest() == initial_sha256


class TestIsSameErrorSignatureHelper:
    """Unit tests for is_same_error_signature helper."""

    def test_matching_signatures(self) -> None:
        sig1 = ErrorSignature(
            exception_type="ZeroDivisionError",
            normalized_message="division by zero",
            top_target_file="app.py",
            top_target_line=10,
        )
        sig2 = ErrorSignature(
            exception_type="ZeroDivisionError",
            normalized_message="division by zero",
            top_target_file="C:/path/to/app.py",
            top_target_line=15,  # shifted line
        )
        assert is_same_error_signature(sig1, sig2) is True

    def test_different_exception_types_do_not_match(self) -> None:
        sig1 = ErrorSignature(
            exception_type="IndexError",
            normalized_message="list index out of range",
            top_target_file="app.py",
            top_target_line=5,
        )
        sig2 = ErrorSignature(
            exception_type="TypeError",
            normalized_message="list indices must be integers",
            top_target_file="app.py",
            top_target_line=5,
        )
        assert is_same_error_signature(sig1, sig2) is False

    def test_different_messages_do_not_match(self) -> None:
        sig1 = ErrorSignature(
            exception_type="ValueError",
            normalized_message="invalid literal for int(): 'a'",
        )
        sig2 = ErrorSignature(
            exception_type="ValueError",
            normalized_message="invalid literal for int(): 'b'",
        )
        assert is_same_error_signature(sig1, sig2) is False

    def test_none_signatures(self) -> None:
        sig = ErrorSignature(exception_type="ValueError", normalized_message="error")
        assert is_same_error_signature(None, None) is True
        assert is_same_error_signature(sig, None) is False
        assert is_same_error_signature(None, sig) is False
