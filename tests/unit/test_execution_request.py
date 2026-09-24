"""Unit and boundary tests for controlled subprocess execution requests.

Verifies:
- Subprocess command formulation with flags -E, -B, -P.
- Working directory (cwd) defaulting to user invocation directory.
- Environment sanitization and allowlist enforcement (PYTHON* stripped).
- Impossibility of shell injection (shell=False invariance).
- Stdin redirection from string and file.
- Explicit boundary tests covering:
  - os.getcwd() matching user invocation directory.
  - __file__ pointing to relocated temporary session copy.
  - relative file access resolving relative to cwd.
  - Path(__file__).parent evaluating to session directory.
  - sys.path inspection verifying -P suppresses ambient cwd/script dir.
  - sibling module imports failing without sys.path modification and succeeding with it.
  - sibling resource access relative to Path(__file__).parent failing intentionally.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from localdev.constants import (
    DEFAULT_OUTPUT_BYTE_CAP,
    DEFAULT_TIMEOUT_SECONDS,
    ENV_ALLOWLIST,
)
from localdev.execution.environment import build_clean_environment, is_allowed_env_var
from localdev.execution.limits import ExecutionLimits
from localdev.execution.runner import (
    ExecutionRequest,
    build_execution_request,
    run_execution_request,
)

# =============================================================================
# Execution Limits Schema Tests
# =============================================================================


def test_execution_limits_defaults() -> None:
    """ExecutionLimits defaults match frozen operational constants."""
    limits = ExecutionLimits()
    assert limits.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert limits.output_byte_cap == DEFAULT_OUTPUT_BYTE_CAP
    assert limits.max_process_memory_bytes is None


def test_execution_limits_validation() -> None:
    """Non-positive limits are rejected with validation error."""
    with pytest.raises(ValidationError):
        ExecutionLimits(timeout_seconds=0.0)

    with pytest.raises(ValidationError):
        ExecutionLimits(output_byte_cap=-1)


# =============================================================================
# Environment Sanitization & Allowlist Tests
# =============================================================================


def test_environment_allowlist_filtering() -> None:
    """Parent environment variables not in allowlist are completely stripped."""
    dirty_env = {
        "SYSTEMROOT": r"C:\Windows",
        "PATH": r"C:\Windows\System32",
        "PYTHONPATH": r"C:\malicious\payload",
        "PYTHONHOME": r"C:\other\python",
        "SECRET_API_KEY": "super-secret-12345",
        "USERNAME": "test_user",
    }
    clean = build_clean_environment(base_env=dirty_env)

    assert "SYSTEMROOT" in clean
    assert "PATH" in clean
    assert "USERNAME" in clean
    assert "PYTHONPATH" not in clean
    assert "PYTHONHOME" not in clean
    assert "SECRET_API_KEY" not in clean
    assert set(clean.keys()).issubset(set(ENV_ALLOWLIST))


def test_environment_case_insensitive_matching() -> None:
    """Windows environment keys with varying case are mapped to canonical allowlist."""
    mixed_env = {
        "systemroot": r"C:\Windows",
        "Path": r"C:\Windows\System32",
        "temp": r"C:\Temp",
        "random_var": "ignored",
    }
    clean = build_clean_environment(base_env=mixed_env)
    assert clean["SYSTEMROOT"] == r"C:\Windows"
    assert clean["PATH"] == r"C:\Windows\System32"
    assert clean["TEMP"] == r"C:\Temp"
    assert "random_var" not in clean


def test_environment_permitted_overrides() -> None:
    """Non-Python environment overrides can be passed explicitly."""
    base_env = {"SYSTEMROOT": r"C:\Windows"}
    clean = build_clean_environment(
        env_overrides={"CUSTOM_FLAG": "enabled"},
        base_env=base_env,
    )
    assert clean["SYSTEMROOT"] == r"C:\Windows"
    assert clean["CUSTOM_FLAG"] == "enabled"


def test_environment_python_overrides_rejected() -> None:
    """Attempting to inject PYTHON* variables via overrides fails closed."""
    with pytest.raises(ValueError, match="prohibited: PYTHON\\* variables cannot be injected"):
        build_clean_environment(
            env_overrides={"PYTHONPATH": r"C:\injected"},
            base_env={},
        )

    with pytest.raises(ValueError, match="prohibited: PYTHON\\* variables cannot be injected"):
        build_clean_environment(
            env_overrides={"pythonhome": r"C:\injected"},
            base_env={},
        )


def test_is_allowed_env_var() -> None:
    """Helper correctly identifies allowlisted variables."""
    assert is_allowed_env_var("PATH") is True
    assert is_allowed_env_var("path") is True
    assert is_allowed_env_var("SYSTEMROOT") is True
    assert is_allowed_env_var("PYTHONPATH") is False
    assert is_allowed_env_var("USERPROFILE") is False


# =============================================================================
# Execution Request Construction Tests
# =============================================================================


def test_build_execution_request_command_structure(tmp_path: Path) -> None:
    """Execution command strictly enforces [python, -E, -B, -P, target, *args]."""
    target = tmp_path / "script.py"
    target.touch()

    req = build_execution_request(
        target_path=target,
        args=["--flag", "value", "123"],
    )

    assert req.command[0] == str(Path(sys.executable).resolve())
    assert req.command[1:4] == ["-E", "-B", "-P"]
    assert req.command[4] == str(target.resolve())
    assert req.command[5:] == ["--flag", "value", "123"]
    assert req.shell is False


def test_build_execution_request_cwd_default_and_custom(tmp_path: Path) -> None:
    """Default cwd is invocation directory (Path.cwd()), overrideable by caller."""
    target = tmp_path / "script.py"
    target.touch()

    default_req = build_execution_request(target_path=target)
    assert default_req.cwd == str(Path.cwd().resolve())

    custom_cwd = tmp_path / "custom_workdir"
    custom_cwd.mkdir()
    custom_req = build_execution_request(target_path=target, cwd=custom_cwd)
    assert custom_req.cwd == str(custom_cwd.resolve())


def test_shell_true_strictly_prohibited(tmp_path: Path) -> None:
    """ExecutionRequest forbids shell=True via schema validation."""
    with pytest.raises(ValidationError):
        ExecutionRequest(
            command=["python", "test.py"],
            cwd=str(tmp_path),
            env={},
            shell=True,  # type: ignore[arg-type]
        )


def test_mutually_exclusive_stdin(tmp_path: Path) -> None:
    """Providing both stdin_data and stdin_file is rejected."""
    target = tmp_path / "test.py"
    target.touch()
    stdin_file = tmp_path / "in.txt"
    stdin_file.touch()

    with pytest.raises(ValueError, match="Cannot specify both 'stdin_data' and 'stdin_file'"):
        build_execution_request(
            target_path=target,
            stdin_data="test",
            stdin_file=stdin_file,
        )


# =============================================================================
# Shell Metacharacter Safety Tests
# =============================================================================


def test_shell_metacharacters_remain_literal_arguments(tmp_path: Path) -> None:
    """Shell metacharacters (&, |, <, >, ;) passed in args remain literal."""
    script = tmp_path / "echo_args.py"
    script.write_text(
        "import sys, json\n"
        "print(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )

    special_args = ["&", "dir", "|", "whoami", ";", "<", ">", "out.txt"]
    req = build_execution_request(target_path=script, args=special_args)
    result = run_execution_request(req)

    assert result.exit_code == 0
    echoed = json.loads(result.stdout.strip())
    assert echoed == special_args
    # Verify no file 'out.txt' was created by redirection
    assert not (Path.cwd() / "out.txt").exists()


# =============================================================================
# Stdin Redirection Tests
# =============================================================================


def test_stdin_redirection_from_string(tmp_path: Path) -> None:
    """Target correctly reads input from string payload via stdin."""
    script = tmp_path / "read_stdin.py"
    script.write_text(
        "import sys\n"
        "data = sys.stdin.read()\n"
        "print(f'READ: {data}')\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=script, stdin_data="hello from memory")
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert result.stdout.strip() == "READ: hello from memory"


def test_stdin_redirection_from_file(tmp_path: Path) -> None:
    """Target correctly reads input redirected from an external file."""
    script = tmp_path / "read_stdin.py"
    script.write_text(
        "import sys\n"
        "data = sys.stdin.read()\n"
        "print(f'READ: {data}')\n",
        encoding="utf-8",
    )

    input_file = tmp_path / "input.txt"
    input_file.write_text("hello from disk file", encoding="utf-8")

    req = build_execution_request(target_path=script, stdin_file=input_file)
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert result.stdout.strip() == "READ: hello from disk file"


# =============================================================================
# Runtime Isolation & Boundary Tests (Contract Section 3.1)
# =============================================================================


def test_boundary_os_getcwd_matches_invocation_dir(tmp_path: Path) -> None:
    """Target executing from session dir preserves caller's working directory."""
    invocation_dir = tmp_path / "workdir"
    invocation_dir.mkdir()

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    script = session_dir / "check_cwd.py"
    script.write_text(
        "import os\n"
        "print(os.getcwd())\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=script, cwd=invocation_dir)
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert Path(result.stdout.strip()).resolve() == invocation_dir.resolve()


def test_boundary_file_points_to_session_copy(tmp_path: Path) -> None:
    """Target __file__ evaluates to relocated temporary session copy, not original source."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    original_target = source_dir / "target.py"
    original_target.write_text("pass", encoding="utf-8")

    session_dir = tmp_path / "session_123"
    session_dir.mkdir()
    session_target = session_dir / "session_target.py"
    session_target.write_text(
        "from pathlib import Path\n"
        "print(Path(__file__).resolve())\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=session_target)
    result = run_execution_request(req)

    assert result.exit_code == 0
    reported_file = Path(result.stdout.strip()).resolve()
    assert reported_file == session_target.resolve()
    assert reported_file != original_target.resolve()


def test_boundary_relative_file_access_from_cwd(tmp_path: Path) -> None:
    """Relative file open calls inside target resolve against invocation cwd."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    data_file = workdir / "app_config.txt"
    data_file.write_text("KEY=VALUE123", encoding="utf-8")

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    script = session_dir / "read_rel.py"
    script.write_text(
        "with open('app_config.txt', 'r', encoding='utf-8') as f:\n"
        "    print(f.read().strip())\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=script, cwd=workdir)
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert result.stdout.strip() == "KEY=VALUE123"


def test_boundary_file_parent_points_to_session_dir(tmp_path: Path) -> None:
    """Path(__file__).parent evaluates to session dir rather than original source dir."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    session_dir = tmp_path / "session_abc"
    session_dir.mkdir()
    script = session_dir / "session_target.py"
    script.write_text(
        "from pathlib import Path\n"
        "print(Path(__file__).parent.resolve())\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=script)
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert Path(result.stdout.strip()).resolve() == session_dir.resolve()
    assert Path(result.stdout.strip()).resolve() != source_dir.resolve()


def test_boundary_sys_path_suppression_with_p_flag(tmp_path: Path) -> None:
    """The -P flag prevents automatic prepending of ambient script or cwd to sys.path."""
    session_dir = tmp_path / "session_xyz"
    session_dir.mkdir()
    invocation_dir = tmp_path / "workdir"
    invocation_dir.mkdir()

    script = session_dir / "inspect_path.py"
    script.write_text(
        "import sys, json\n"
        "print(json.dumps(sys.path))\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=script, cwd=invocation_dir)
    result = run_execution_request(req)

    assert result.exit_code == 0
    sys_path_entries = [os.path.normcase(p) for p in json.loads(result.stdout.strip())]

    # Invariant: Neither empty string ('') nor invocation_dir nor session_dir is auto-prepended
    assert "" not in sys_path_entries
    assert os.path.normcase(str(invocation_dir)) not in sys_path_entries
    assert os.path.normcase(str(session_dir)) not in sys_path_entries

    # Standard library / site-packages remain reachable
    assert any("site-packages" in p or "lib" in p for p in sys_path_entries)


def test_boundary_sibling_module_import_fails_without_sys_path_modification(
    tmp_path: Path,
) -> None:
    """Single-target execution does not copy sibling files; ambient import fails."""
    source_dir = tmp_path / "project"
    source_dir.mkdir()

    # Original project has target.py and sibling.py
    sibling = source_dir / "sibling.py"
    sibling.write_text("def greet(): return 'hello'", encoding="utf-8")

    # Session only receives the single target copy
    session_dir = tmp_path / "session_target_only"
    session_dir.mkdir()
    target_copy = session_dir / "session_target.py"
    target_copy.write_text(
        "import sibling\n"
        "print(sibling.greet())\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=target_copy, cwd=source_dir)
    result = run_execution_request(req)

    # Because -P suppressed source_dir and sibling was not copied, import fails
    assert result.exit_code != 0
    assert "ModuleNotFoundError" in result.stderr
    assert "No module named 'sibling'" in result.stderr


def test_boundary_sibling_module_import_succeeds_with_programmatic_sys_path(
    tmp_path: Path,
) -> None:
    """Target can programmatically modify sys.path at runtime (not a hermetic sandbox)."""
    source_dir = tmp_path / "project"
    source_dir.mkdir()

    sibling = source_dir / "sibling.py"
    sibling.write_text("def greet(): return 'hello from sibling'", encoding="utf-8")

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    target_copy = session_dir / "session_target.py"
    target_copy.write_text(
        f"import sys\n"
        f"sys.path.insert(0, {str(source_dir)!r})\n"
        f"import sibling\n"
        f"print(sibling.greet())\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=target_copy)
    result = run_execution_request(req)

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello from sibling"


def test_boundary_sibling_resource_access_fails_without_copying(tmp_path: Path) -> None:
    """Accessing resources relative to Path(__file__).parent fails intentionally."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    resource_file = source_dir / "config.json"
    resource_file.write_text('{"status": "ok"}', encoding="utf-8")

    session_dir = tmp_path / "session_copy"
    session_dir.mkdir()
    target_copy = session_dir / "session_target.py"
    target_copy.write_text(
        "from pathlib import Path\n"
        "resource = Path(__file__).parent / 'config.json'\n"
        "with open(resource, 'r', encoding='utf-8') as f:\n"
        "    print(f.read())\n",
        encoding="utf-8",
    )

    req = build_execution_request(target_path=target_copy)
    result = run_execution_request(req)

    # Contract invariant: single target does not copy resources beside the original
    assert result.exit_code != 0
    assert "FileNotFoundError" in result.stderr
