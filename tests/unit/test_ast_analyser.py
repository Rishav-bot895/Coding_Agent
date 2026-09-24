"""Unit tests for AST fact extraction, structural analysis, and selector matching.

Verifies:
1. Functions discovery: sync, async, parameters, docstrings.
2. Classes & methods discovery: qualified names, nested classes, methods list.
3. Inclusive 1-based line ranges within file boundaries.
4. Decorators: stacked, multiline, zero execution side effects.
5. Multiline signatures and comprehensions.
6. Function body structural facts: loops, branches, calls, returns, recursion.
7. Function selector parsing, validation, matching, and resolution.
8. Short-circuit on syntax errors.
9. Integration with PythonAdapter.extract_ast_facts.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from localdev.agent.permissions import validate_target
from localdev.errors import MalformedSelectorError, SelectorNotFoundError
from localdev.languages.python.adapter import PythonAdapter
from localdev.languages.python.ast_analyser import (
    analyse_function_structure,
    extract_ast_facts,
    extract_ast_facts_from_source,
)
from localdev.languages.python.selectors import (
    match_function_selector,
    parse_function_selector,
    resolve_function_selector,
)
from localdev.schemas import TargetRecord

FIXTURES_DIR = Path(__file__).parent.parent / "complexity_samples" / "ast"


def test_functions_and_classes_extraction() -> None:
    fixture = FIXTURES_DIR / "functions_and_classes.py"
    target = validate_target(str(fixture))

    facts = extract_ast_facts(target)

    assert facts.syntax_error is None
    assert facts.total_lines > 0

    # Verify functions
    func_names = [f.name for f in facts.functions]
    assert "standalone_func" in func_names
    assert "simple_utility" in func_names
    assert "__init__" in func_names
    assert "add" in func_names
    assert "create_default" in func_names
    assert "is_positive" in func_names
    assert "compute" in func_names

    # Check standalone_func parameter extraction
    standalone = next(f for f in facts.functions if f.name == "standalone_func")
    assert standalone.qualified_name == "standalone_func"
    assert standalone.is_method is False
    assert standalone.is_async is False
    assert standalone.parameters == ["x", "y", "*args", "z", "**kwargs"]
    assert standalone.docstring == "A standalone function with comprehensive parameters."
    assert 1 <= standalone.start_line <= standalone.end_line <= facts.total_lines

    # Check class discovery
    class_names = [c.name for c in facts.classes]
    assert "Calculator" in class_names
    assert "Calculator.SubEngine" in class_names

    calc_class = next(c for c in facts.classes if c.name == "Calculator")
    assert "add" in calc_class.methods
    assert "__init__" in calc_class.methods
    assert 1 <= calc_class.start_line <= calc_class.end_line <= facts.total_lines

    # Check method qualified names
    add_method = next(f for f in facts.functions if f.qualified_name == "Calculator.add")
    assert add_method.is_method is True
    assert add_method.parameters == ["self", "amount"]

    nested_method = next(f for f in facts.functions if f.qualified_name == "Calculator.SubEngine.compute")
    assert nested_method.is_method is True
    assert nested_method.parameters == ["self", "factor"]


def test_async_and_decorators_extraction() -> None:
    fixture = FIXTURES_DIR / "async_and_decorators.py"
    target = validate_target(str(fixture))

    facts = extract_ast_facts(target)
    assert facts.syntax_error is None

    # Async function check
    async_fetch = next(f for f in facts.functions if f.name == "async_fetch")
    assert async_fetch.is_async is True
    assert async_fetch.is_method is False
    assert async_fetch.parameters == ["url", "timeout"]

    # Async method check
    async_proc = next(f for f in facts.functions if f.qualified_name == "AsyncService.async_process")
    assert async_proc.is_async is True
    assert async_proc.is_method is True

    # Decorated function start line should encompass decorators
    dec_func = next(f for f in facts.functions if f.name == "decorated_function")
    source_lines = fixture.read_text("utf-8").splitlines()
    first_dec_line = next(i + 1 for i, l in enumerate(source_lines) if "@my_decorator" in l)
    assert dec_func.start_line <= first_dec_line
    assert dec_func.start_line <= dec_func.end_line <= facts.total_lines

    # Decorated class
    async_class = next(c for c in facts.classes if c.name == "AsyncService")
    assert async_class.start_line <= async_class.end_line <= facts.total_lines


def test_multiline_signatures() -> None:
    fixture = FIXTURES_DIR / "multiline_signatures.py"
    target = validate_target(str(fixture))

    facts = extract_ast_facts(target)
    assert facts.syntax_error is None

    multiline_func = next(f for f in facts.functions if f.name == "complex_multiline_func")
    assert multiline_func.parameters == [
        "first_arg",
        "second_arg",
        "*var_args",
        "keyword_only_one",
        "keyword_only_two",
        "**remaining_kwargs",
    ]
    assert 1 <= multiline_func.start_line <= multiline_func.end_line <= facts.total_lines

    method = next(f for f in facts.functions if f.qualified_name == "DataProcessor.process_records")
    assert method.parameters == ["self", "records", "filter_mode", "batch_size"]
    assert 1 <= method.start_line <= method.end_line <= facts.total_lines


def test_comprehensions_and_nested_functions() -> None:
    fixture = FIXTURES_DIR / "comprehensions_and_nested.py"
    target = validate_target(str(fixture))

    facts = extract_ast_facts(target)
    assert facts.syntax_error is None

    outer_func = next(f for f in facts.functions if f.name == "outer_processor")
    assert 1 <= outer_func.start_line <= outer_func.end_line <= facts.total_lines

    # Structural body analysis on outer_processor
    tree = ast.parse(fixture.read_text("utf-8"))
    fn_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "outer_processor")
    struct = analyse_function_structure(fn_node)

    assert struct.name == "outer_processor"
    assert struct.nested_functions == ["inner_helper"]
    assert "ListComp" in struct.comprehensions
    assert "SetComp" in struct.comprehensions
    assert "DictComp" in struct.comprehensions
    assert "GeneratorExp" in struct.comprehensions
    assert struct.max_loop_depth == 2  # for containing while
    assert len(struct.loops) >= 2
    assert len(struct.branches) >= 2
    assert len(struct.returns) >= 2


def test_recursive_function_structure() -> None:
    fixture = FIXTURES_DIR / "comprehensions_and_nested.py"
    tree = ast.parse(fixture.read_text("utf-8"))
    fn_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "recursive_factorial")
    struct = analyse_function_structure(fn_node)

    assert struct.has_recursion is True
    rec_calls = [c for c in struct.calls if c.is_recursive]
    assert len(rec_calls) == 1
    assert rec_calls[0].callee == "recursive_factorial"


def test_syntax_error_short_circuit() -> None:
    sample_broken = FIXTURES_DIR / "syntax_error.py.sample"
    content = sample_broken.read_text("utf-8")

    facts = extract_ast_facts_from_source(content, filename="broken.py")

    assert facts.syntax_error is not None
    assert facts.functions == []
    assert facts.classes == []
    assert facts.total_lines > 0


def test_empty_source_facts() -> None:
    facts = extract_ast_facts_from_source("", filename="empty.py")
    assert facts.functions == []
    assert facts.classes == []
    assert facts.total_lines == 0
    assert facts.syntax_error is None


def test_selector_parsing_valid() -> None:
    sel1 = parse_function_selector("calc.py::add")
    assert sel1.file_path == "calc.py"
    assert sel1.function_name == "add"
    assert sel1.class_name is None
    assert sel1.qualified_name == "add"

    sel2 = parse_function_selector("path/to/calc.py::Calculator.compute")
    assert sel2.file_path == "path/to/calc.py"
    assert sel2.function_name == "compute"
    assert sel2.class_name == "Calculator"
    assert sel2.qualified_name == "Calculator.compute"

    sel3 = parse_function_selector("my_func")
    assert sel3.file_path is None
    assert sel3.function_name == "my_func"
    assert sel3.class_name is None

    sel4 = parse_function_selector("Calculator.add")
    assert sel4.file_path is None
    assert sel4.function_name == "add"
    assert sel4.class_name == "Calculator"


@pytest.mark.parametrize(
    "invalid_selector",
    [
        "",
        "   ",
        "::",
        "::func",
        "file.py::",
        "file.py::a::b",
        "file.py::123invalid",
        "file.py::func-name",
        "file.py::func..bad",
        "file.py::.func",
        "file.py::func.",
    ],
)
def test_selector_parsing_invalid(invalid_selector: str) -> None:
    with pytest.raises(MalformedSelectorError):
        parse_function_selector(invalid_selector)


def test_nested_function_selector_rejected() -> None:
    fixture = FIXTURES_DIR / "comprehensions_and_nested.py"
    target = validate_target(str(fixture))
    facts = extract_ast_facts(target)

    # outer_processor contains inner_helper
    with pytest.raises(MalformedSelectorError) as exc_info:
        match_function_selector("outer_processor.inner_helper", facts)
    assert "nested functions are not supported in selectors" in str(exc_info.value)


def test_selector_matching_and_resolution() -> None:
    fixture = FIXTURES_DIR / "functions_and_classes.py"
    target = validate_target(str(fixture))
    facts = extract_ast_facts(target)

    # Top-level function match
    f1 = resolve_function_selector("standalone_func", facts, target_path=str(fixture))
    assert f1.name == "standalone_func"
    assert f1.qualified_name == "standalone_func"

    # Match with file.py::
    f2 = resolve_function_selector("functions_and_classes.py::simple_utility", facts, target_path=str(fixture))
    assert f2.name == "simple_utility"

    # Class method match
    m1 = resolve_function_selector("Calculator.add", facts, target_path=str(fixture))
    assert m1.qualified_name == "Calculator.add"

    # Match method by simple name when unique
    m2 = resolve_function_selector("add", facts, target_path=str(fixture))
    assert m2.qualified_name == "Calculator.add"

    # Nested class method match
    m3 = resolve_function_selector("Calculator.SubEngine.compute", facts, target_path=str(fixture))
    assert m3.qualified_name == "Calculator.SubEngine.compute"

    # Selector with wrong file path returns None
    no_match = match_function_selector("wrong_file.py::standalone_func", facts, target_path=str(fixture))
    assert no_match is None

    # Resolution failure raises SelectorNotFoundError with available candidates
    with pytest.raises(SelectorNotFoundError) as exc_info:
        resolve_function_selector("non_existent_func", facts, target_path=str(fixture))
    assert "standalone_func" in exc_info.value.available_selectors


def test_ambiguous_method_selector() -> None:
    source = """
class A:
    def execute(self): pass

class B:
    def execute(self): pass
"""
    facts = extract_ast_facts_from_source(source)
    # Matching simple name "execute" should detect ambiguity
    with pytest.raises(MalformedSelectorError) as exc_info:
        match_function_selector("execute", facts)
    assert "Ambiguous selector" in str(exc_info.value)

    # Qualified selectors should resolve cleanly
    a_exec = resolve_function_selector("A.execute", facts)
    assert a_exec.qualified_name == "A.execute"
    b_exec = resolve_function_selector("B.execute", facts)
    assert b_exec.qualified_name == "B.execute"


def test_adapter_extract_ast_facts_integration() -> None:
    adapter = PythonAdapter()
    fixture = FIXTURES_DIR / "functions_and_classes.py"
    target = validate_target(str(fixture))

    facts = adapter.extract_ast_facts(target)
    assert facts.syntax_error is None
    assert len(facts.functions) > 0
    assert len(facts.classes) > 0

    # With source override
    override_facts = adapter.extract_ast_facts(target, source_text="def override_fn(): pass\n")
    assert len(override_facts.functions) == 1
    assert override_facts.functions[0].name == "override_fn"


def test_missing_target_file_ast_facts() -> None:
    dummy_target = TargetRecord(
        path="missing.py",
        absolute_path=r"C:\non_existent\missing.py",
        file_size_bytes=0,
        sha256="0" * 64,
        encoding="utf-8",
        has_bom=False,
        newline_style="\n",
        has_trailing_newline=False,
        is_read_only=False,
        is_reparse_point=False,
    )
    facts = extract_ast_facts(dummy_target)
    assert facts.syntax_error is not None
    assert "Target file not found" in facts.syntax_error
