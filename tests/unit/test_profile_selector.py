"""Unit tests for profiling function selector parsing and resolution (Phase 11 Task P11-T1).

Tests:
1. Module function selector
2. Class method selector (regular method, classmethod, staticmethod)
3. Missing selector (raises SelectorNotFoundError)
4. Ambiguous selector (raises MalformedSelectorError)
5. Unsupported nested function selector (raises MalformedSelectorError)
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from localdev.errors import MalformedSelectorError, SelectorNotFoundError
from localdev.languages.python.ast_analyser import extract_ast_facts_from_source
from localdev.languages.python.selectors import (
    parse_function_selector,
    resolve_callable_from_module,
    resolve_function_selector,
    resolve_selector_from_file,
)

# =============================================================================
# Selector Parsing Tests
# =============================================================================


def test_parse_function_selector_top_level() -> None:
    """Verify parsing top-level function selectors with and without file paths."""
    sel1 = parse_function_selector("compute_total")
    assert sel1.file_path is None
    assert sel1.class_name is None
    assert sel1.function_name == "compute_total"
    assert sel1.qualified_name == "compute_total"

    sel2 = parse_function_selector("utils/math_ops.py::compute_total")
    assert sel2.file_path == "utils/math_ops.py"
    assert sel2.class_name is None
    assert sel2.function_name == "compute_total"


def test_parse_function_selector_class_method() -> None:
    """Verify parsing class method selectors with and without file paths."""
    sel1 = parse_function_selector("Calculator.add")
    assert sel1.file_path is None
    assert sel1.class_name == "Calculator"
    assert sel1.function_name == "add"
    assert sel1.qualified_name == "Calculator.add"

    sel2 = parse_function_selector("calc.py::Calculator.add")
    assert sel2.file_path == "calc.py"
    assert sel2.class_name == "Calculator"
    assert sel2.function_name == "add"


def test_parse_function_selector_syntax_rejections() -> None:
    """Verify malformed selector strings raise MalformedSelectorError."""
    with pytest.raises(MalformedSelectorError, match="cannot be empty"):
        parse_function_selector("")

    with pytest.raises(MalformedSelectorError, match="cannot be empty"):
        parse_function_selector("   ")

    with pytest.raises(MalformedSelectorError, match="multiple '::'"):
        parse_function_selector("a.py::b::c")

    with pytest.raises(MalformedSelectorError, match="missing target file path"):
        parse_function_selector("::func")

    with pytest.raises(MalformedSelectorError, match="missing function name"):
        parse_function_selector("a.py::")

    with pytest.raises(MalformedSelectorError, match="not a valid Python identifier"):
        parse_function_selector("123invalid")


# =============================================================================
# AST Facts Selector Resolution Tests
# =============================================================================

SAMPLE_SOURCE = """
def standalone_func(x: int) -> int:
    return x * 2

def outer_func():
    def inner_nested():
        return 42
    return inner_nested()

class Engine:
    def start(self) -> str:
        return "vroom"

    def stop(self) -> str:
        return "quiet"

class Motor:
    def start(self) -> str:
        return "buzz"
"""


def test_ast_selector_top_level_function() -> None:
    """Verify resolving top-level function via AST facts."""
    facts = extract_ast_facts_from_source(SAMPLE_SOURCE, filename="sample.py")
    res = resolve_function_selector("standalone_func", facts, target_path="sample.py")
    assert res.name == "standalone_func"
    assert res.qualified_name == "standalone_func"
    assert not res.is_method


def test_ast_selector_class_method() -> None:
    """Verify resolving class method via AST facts."""
    facts = extract_ast_facts_from_source(SAMPLE_SOURCE, filename="sample.py")
    res = resolve_function_selector("Engine.start", facts, target_path="sample.py")
    assert res.name == "start"
    assert res.qualified_name == "Engine.start"
    assert res.is_method


def test_ast_selector_missing() -> None:
    """Verify missing function or method raises SelectorNotFoundError."""
    facts = extract_ast_facts_from_source(SAMPLE_SOURCE, filename="sample.py")
    with pytest.raises(SelectorNotFoundError, match="not found in target"):
        resolve_function_selector("non_existent_func", facts, target_path="sample.py")


def test_ast_selector_ambiguous_method() -> None:
    """Verify ambiguous unqualified method on multiple classes raises MalformedSelectorError."""
    facts = extract_ast_facts_from_source(SAMPLE_SOURCE, filename="sample.py")
    with pytest.raises(
        MalformedSelectorError, match="Ambiguous selector 'start' matches multiple methods"
    ):
        resolve_function_selector("start", facts, target_path="sample.py")


def test_ast_selector_nested_function_rejected() -> None:
    """Verify selector targeting nested function inside a function raises MalformedSelectorError."""
    facts = extract_ast_facts_from_source(SAMPLE_SOURCE, filename="sample.py")
    with pytest.raises(MalformedSelectorError, match="nested functions are not supported"):
        resolve_function_selector("outer_func.inner_nested", facts, target_path="sample.py")


def test_resolve_selector_from_file_helper(tmp_path: Path) -> None:
    """Verify resolve_selector_from_file convenience function."""
    target = tmp_path / "code.py"
    target.write_text("def ping() -> str:\n    return 'pong'\n", encoding="utf-8")
    fact = resolve_selector_from_file(target, "ping")
    assert fact.name == "ping"
    assert fact.qualified_name == "ping"


# =============================================================================
# Live Module Callable Resolution Tests
# =============================================================================


def _create_test_module() -> types.ModuleType:
    """Create a live in-memory module object for testing callable resolution."""
    mod = types.ModuleType("test_mod")

    def standalone(a: int, b: int) -> int:
        return a + b

    def outer():
        def inner():
            return 99
        return inner

    class Calculator:
        def add(self, a: int, b: int) -> int:
            return a + b

        @classmethod
        def create(cls) -> Calculator:
            return cls()

        @staticmethod
        def helper(x: int) -> int:
            return x * 10

    class Multiplier:
        def add(self, a: int, b: int) -> int:
            return a * b

        def multiply(self, a: int, b: int) -> int:
            return a * b

    mod.__dict__["standalone"] = standalone
    mod.__dict__["outer"] = outer
    mod.__dict__["Calculator"] = Calculator
    mod.__dict__["Multiplier"] = Multiplier
    Calculator.__module__ = "test_mod"
    Multiplier.__module__ = "test_mod"
    return mod


def test_module_resolve_top_level_function() -> None:
    """Verify resolving top-level function from live module."""
    mod = _create_test_module()
    func, qualname, is_method = resolve_callable_from_module(mod, "standalone")
    assert callable(func)
    assert func(3, 4) == 7
    assert qualname == "standalone"
    assert not is_method


def test_module_resolve_class_method() -> None:
    """Verify resolving class method from live module."""
    mod = _create_test_module()
    method, qualname, is_method = resolve_callable_from_module(mod, "Calculator.add")
    assert callable(method)
    assert qualname == "Calculator.add"
    assert is_method


def test_module_resolve_classmethod_and_staticmethod() -> None:
    """Verify resolving @classmethod and @staticmethod from live module."""
    mod = _create_test_module()
    cls_method, qual1, is_m1 = resolve_callable_from_module(mod, "Calculator.create")
    assert callable(cls_method)
    assert qual1 == "Calculator.create"
    assert is_m1

    static_method, qual2, is_m2 = resolve_callable_from_module(mod, "Calculator.helper")
    assert callable(static_method)
    assert static_method(5) == 50
    assert qual2 == "Calculator.helper"
    assert is_m2


def test_module_resolve_unique_bare_method() -> None:
    """Verify resolving bare method name that exists on only one class."""
    mod = _create_test_module()
    method, qualname, is_method = resolve_callable_from_module(mod, "multiply")
    assert callable(method)
    assert qualname == "Multiplier.multiply"
    assert is_method


def test_module_resolve_ambiguous_bare_method_rejected() -> None:
    """Verify bare method name on multiple classes raises MalformedSelectorError."""
    mod = _create_test_module()
    with pytest.raises(
        MalformedSelectorError, match="Ambiguous selector 'add' matches multiple methods"
    ):
        resolve_callable_from_module(mod, "add")


def test_module_resolve_missing_selector() -> None:
    """Verify missing function or method raises SelectorNotFoundError."""
    mod = _create_test_module()
    with pytest.raises(SelectorNotFoundError, match="not found in target module"):
        resolve_callable_from_module(mod, "missing_function")

    with pytest.raises(SelectorNotFoundError, match="not found on class 'Calculator'"):
        resolve_callable_from_module(mod, "Calculator.missing_method")


def test_module_resolve_nested_function_rejected() -> None:
    """Verify selector targeting nested function inside a function raises MalformedSelectorError."""
    mod = _create_test_module()
    with pytest.raises(
        MalformedSelectorError, match="nested functions are not supported in selectors"
    ):
        resolve_callable_from_module(mod, "outer.inner")


def test_module_resolve_file_path_mismatch() -> None:
    """Verify selector with mismatched file path raises SelectorNotFoundError."""
    mod = _create_test_module()
    with pytest.raises(SelectorNotFoundError, match="does not match target file"):
        resolve_callable_from_module(mod, "other.py::standalone", target_path="main.py")
