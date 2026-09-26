"""Unit tests for profiling JSON inputs validation and mutation protection (Phase 11 Task P11-T2).

Tests:
1. Schema parsing: { "args": [...], "kwargs": {...} }.
2. Defaulting: empty object, args-only, kwargs-only.
3. Argument re-creation policy: fresh deep copies on every run.
4. Mutation protection: mutating functions (sort, pop, clear) receive identical fresh arguments.
5. Limits enforcement: 1 MB maximum input size and 20 levels maximum nesting depth.
6. Validation rejections: non-objects, malformed JSON, invalid kwarg identifiers, oversized, deeply nested.
7. Worker integration with --input flag.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from localdev.constants import MAX_PROFILE_INPUT_BYTES, MAX_PROFILE_INPUT_DEPTH
from localdev.errors import ProfileInputError
from localdev.profiling.benchmark import (
    ProfileInput,
    ProfileInputManager,
    compute_json_depth,
)
from localdev.profiling.loader import load_target_in_worker

FIXTURES_DIR = Path("tests/profiling_samples/inputs")


# =============================================================================
# Depth Computation Tests
# =============================================================================


def test_compute_json_depth_primitives() -> None:
    """Verify depth of primitives is 1."""
    assert compute_json_depth("hello") == 1
    assert compute_json_depth(42) == 1
    assert compute_json_depth(True) == 1
    assert compute_json_depth(None) == 1


def test_compute_json_depth_empty_containers() -> None:
    """Verify depth of empty dict or list is 1."""
    assert compute_json_depth({}) == 1
    assert compute_json_depth([]) == 1


def test_compute_json_depth_nested() -> None:
    """Verify accurate depth calculation for nested structures."""
    # {"a": [1, 2]} -> depth 3 (dict -> list -> primitive)
    assert compute_json_depth({"a": [1, 2]}) == 3
    # {"a": {"b": [1, {"c": 2}]}} -> dict(1) -> dict(2) -> list(3) -> dict(4) -> primitive(5)
    assert compute_json_depth({"a": {"b": [1, {"c": 2}]}}) == 5


# =============================================================================
# Valid Input Schema & Defaulting Tests
# =============================================================================


def test_profile_input_valid_full() -> None:
    """Verify parsing valid args and kwargs."""
    raw_model = ProfileInput(args=[1, "two", 3.0], kwargs={"key": "val"})
    assert raw_model.args == [1, "two", 3.0]
    assert raw_model.kwargs == {"key": "val"}

    mgr = ProfileInputManager('{"args": [1, "two", 3.0], "kwargs": {"key": "val"}}')
    assert isinstance(mgr.model, ProfileInput)
    assert mgr.args == [1, "two", 3.0]
    assert mgr.kwargs == {"key": "val"}


def test_profile_input_empty_object() -> None:
    """Verify empty object defaults to empty args list and empty kwargs dict."""
    mgr = ProfileInputManager("{}")
    assert mgr.args == []
    assert mgr.kwargs == {}


def test_profile_input_args_only() -> None:
    """Verify args only defaults kwargs to empty dict."""
    mgr = ProfileInputManager('{"args": [100, 200]}')
    assert mgr.args == [100, 200]
    assert mgr.kwargs == {}


def test_profile_input_kwargs_only() -> None:
    """Verify kwargs only defaults args to empty list."""
    mgr = ProfileInputManager('{"kwargs": {"limit": 10}}')
    assert mgr.args == []
    assert mgr.kwargs == {"limit": 10}


def test_profile_input_from_file_fixtures() -> None:
    """Verify loading from created sample JSON fixture files."""
    mgr_valid = ProfileInputManager.from_file(FIXTURES_DIR / "valid_args.json")
    assert mgr_valid.args == [10, 20, 30]
    assert mgr_valid.kwargs == {"label": "benchmark", "verbose": True}

    mgr_empty = ProfileInputManager.from_file(FIXTURES_DIR / "empty.json")
    assert mgr_empty.args == []
    assert mgr_empty.kwargs == {}

    mgr_args = ProfileInputManager.from_file(FIXTURES_DIR / "args_only.json")
    assert mgr_args.args == ["alpha", "beta", 42]

    mgr_kwargs = ProfileInputManager.from_file(FIXTURES_DIR / "kwargs_only.json")
    assert mgr_kwargs.kwargs == {"timeout": 5.0, "enabled": False}

    mgr_nested = ProfileInputManager.from_file(FIXTURES_DIR / "nested_valid.json")
    assert len(mgr_nested.args) == 1


# =============================================================================
# Re-Creation Policy & Mutation Protection Tests
# =============================================================================


def test_recreate_arguments_mutation_isolation() -> None:
    """Verify recreate_arguments returns completely isolated deep copies."""
    mgr = ProfileInputManager('{"args": [[5, 4, 3, 2, 1]], "kwargs": {"cfg": {"flag": true}}}')

    args1, kwargs1 = mgr.recreate_arguments()
    args2, kwargs2 = mgr.recreate_arguments()

    # Mutate first set of arguments
    args1[0].append(999)
    args1[0].sort()
    kwargs1["cfg"]["flag"] = False
    kwargs1["extra"] = "injected"

    # Verify second set remains completely unmutated
    assert args2[0] == [5, 4, 3, 2, 1]
    assert kwargs2["cfg"]["flag"] is True
    assert "extra" not in kwargs2

    # Verify manager internal state remains unmutated
    assert mgr.args[0] == [5, 4, 3, 2, 1]
    assert mgr.kwargs["cfg"]["flag"] is True


def test_mutating_function_simulation() -> None:
    """Simulate a benchmark loop with a function that violently mutates its arguments."""
    raw = json.dumps({
        "args": [[10, 2, 8, 1, 9, 3]],
        "kwargs": {"options": {"remove_first": True, "count": 6}},
    })
    mgr = ProfileInputManager(raw)

    def mutating_target(numbers: list[int], options: dict[str, object]) -> int:
        """In-place sort, pop, and dictionary mutation."""
        numbers.sort()
        val = numbers.pop(0)
        options.clear()
        return val

    # Execute 5 consecutive simulated invocations (2 warm-ups + 3 measured runs)
    for run_idx in range(5):
        fresh_args, fresh_kwargs = mgr.recreate_arguments()

        # Before mutation: verify pristine initial state on every run
        assert fresh_args[0] == [10, 2, 8, 1, 9, 3], f"Run {run_idx} did not receive initial list!"
        assert fresh_kwargs["options"] == {"remove_first": True, "count": 6}

        result = mutating_target(*fresh_args, **fresh_kwargs)
        assert result == 1  # 1 is smallest after sort

        # After mutation: fresh_args was mutated
        assert fresh_args[0] != [10, 2, 8, 1, 9, 3]
        assert fresh_kwargs["options"] == {}


# =============================================================================
# Validation Rejections & Limit Breaches
# =============================================================================


def test_reject_empty_or_whitespace_string() -> None:
    """Verify empty or whitespace-only JSON string raises ProfileInputError."""
    with pytest.raises(ProfileInputError, match="cannot be empty or whitespace"):
        ProfileInputManager("")

    with pytest.raises(ProfileInputError, match="cannot be empty or whitespace"):
        ProfileInputManager("   \n\t  ")


def test_reject_malformed_json() -> None:
    """Verify invalid JSON syntax raises ProfileInputError."""
    with pytest.raises(ProfileInputError, match="Malformed JSON"):
        ProfileInputManager('{"args": [1, 2, }')


def test_reject_top_level_non_object() -> None:
    """Verify non-dictionary top-level JSON values are rejected."""
    with pytest.raises(ProfileInputError, match="must be a JSON object"):
        ProfileInputManager("[1, 2, 3]")

    with pytest.raises(ProfileInputError, match="must be a JSON object"):
        ProfileInputManager('"a string"')

    with pytest.raises(ProfileInputError, match="must be a JSON object"):
        ProfileInputManager("12345")

    with pytest.raises(ProfileInputError, match="must be a JSON object"):
        ProfileInputManager("null")


def test_reject_unknown_top_level_keys() -> None:
    """Verify unknown keys outside 'args' and 'kwargs' are rejected."""
    with pytest.raises(ProfileInputError, match="schema validation failed"):
        ProfileInputManager('{"args": [], "kwargs": {}, "extra_field": 1}')


def test_reject_invalid_args_type() -> None:
    """Verify 'args' must be a list/array."""
    with pytest.raises(ProfileInputError, match="schema validation failed"):
        ProfileInputManager('{"args": "not a list"}')


def test_reject_invalid_kwargs_type() -> None:
    """Verify 'kwargs' must be an object/dict."""
    with pytest.raises(ProfileInputError, match="schema validation failed"):
        ProfileInputManager('{"kwargs": ["not", "a", "dict"]}')


def test_reject_invalid_kwarg_identifier() -> None:
    """Verify kwarg keys that are not valid Python identifiers are rejected."""
    with pytest.raises(ProfileInputError, match="must be a valid Python identifier"):
        ProfileInputManager('{"kwargs": {"123bad": 1}}')

    with pytest.raises(ProfileInputError, match="must be a valid Python identifier"):
        ProfileInputManager('{"kwargs": {"with space": 1}}')

    with pytest.raises(ProfileInputError, match="must be a valid Python identifier"):
        ProfileInputManager('{"kwargs": {"dashed-name": 1}}')


def test_reject_oversized_input_file(tmp_path: Path) -> None:
    """Verify input file exceeding 1 MB is rejected."""
    oversized_file = tmp_path / "oversized.json"
    # Create payload > 1 MB (1024 * 1024 + 10 bytes)
    big_padding = "x" * (MAX_PROFILE_INPUT_BYTES + 10)
    oversized_file.write_text(json.dumps({"args": [big_padding]}), encoding="utf-8")

    with pytest.raises(ProfileInputError, match="exceeds maximum allowed limit of 1 MB"):
        ProfileInputManager.from_file(oversized_file)


def test_reject_oversized_input_string() -> None:
    """Verify raw JSON string exceeding 1 MB is rejected."""
    big_padding = "x" * (MAX_PROFILE_INPUT_BYTES + 10)
    raw = json.dumps({"args": [big_padding]})

    with pytest.raises(ProfileInputError, match="exceeds maximum allowed size of 1 MB"):
        ProfileInputManager(raw)


def test_reject_deeply_nested_input() -> None:
    """Verify JSON structure exceeding 20 levels of nesting is rejected."""
    # Build structure with 22 levels of nesting
    nested: dict[str, object] = {"val": 1}
    for _ in range(22):
        nested = {"child": nested}

    deep_json = json.dumps({"args": [nested]})
    with pytest.raises(ProfileInputError, match=f"nesting depth .* exceeds maximum limit of {MAX_PROFILE_INPUT_DEPTH}"):
        ProfileInputManager(deep_json)


def test_reject_non_existent_input_file() -> None:
    """Verify non-existent input file raises ProfileInputError."""
    with pytest.raises(ProfileInputError, match="does not exist"):
        ProfileInputManager.from_file("missing_input_file_98765.json")


def test_reject_non_utf8_input_file(tmp_path: Path) -> None:
    """Verify non-UTF8 encoded input file raises ProfileInputError."""
    bad_file = tmp_path / "latin1.json"
    bad_file.write_bytes(b'{"args": ["\xe9\xe8\xe0"]}')  # Latin-1 accented bytes

    with pytest.raises(ProfileInputError, match="must be UTF-8 encoded"):
        ProfileInputManager.from_file(bad_file)


# =============================================================================
# Worker Subprocess Integration with --input Tests
# =============================================================================


def test_worker_load_with_valid_input_file(tmp_path: Path) -> None:
    """Verify worker subprocess accepts valid --input file and reports input_valid=True."""
    target = tmp_path / "sum_service.py"
    target.write_text(
        "def compute_sum(a: int, b: int, multiplier: int = 1) -> int:\n"
        "    return (a + b) * multiplier\n",
        encoding="utf-8",
    )

    input_file = tmp_path / "inputs.json"
    input_file.write_text(
        json.dumps({"args": [5, 15], "kwargs": {"multiplier": 2}}),
        encoding="utf-8",
    )

    res = load_target_in_worker(target, selector="compute_sum", input_file=input_file)
    assert res.success is True
    assert res.input_valid is True
    assert res.args_count == 2
    assert res.kwargs_keys == ["multiplier"]
    assert res.exit_code == 0


def test_worker_load_with_invalid_input_file_reports_error(tmp_path: Path) -> None:
    """Verify worker subprocess gracefully reports ProfileInputError for malformed input."""
    target = tmp_path / "sum_service.py"
    target.write_text("def ping() -> str: return 'pong'\n", encoding="utf-8")

    input_file = tmp_path / "bad_inputs.json"
    input_file.write_text('{"kwargs": {"123illegal": "value"}}', encoding="utf-8")

    res = load_target_in_worker(target, selector="ping", input_file=input_file)
    assert res.success is False
    assert res.error_type == "ProfileInputError"
    assert "must be a valid Python identifier" in (res.error_message or "")
