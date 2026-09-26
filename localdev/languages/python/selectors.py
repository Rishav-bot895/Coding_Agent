"""Function selector parsing, validation, and AST matching for Python targets.

Implements the contract:
- file.py::function_name
- file.py::ClassName.method_name
- function_name
- ClassName.method_name

Rejects malformed selectors and explicitly disallows nested functions in selectors.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from localdev.errors import MalformedSelectorError, SelectorNotFoundError

if TYPE_CHECKING:
    from localdev.schemas import ASTFacts, ASTFunctionFact


@dataclass(frozen=True)
class FunctionSelector:
    """Structured representation of a parsed function/method selector."""

    file_path: str | None
    function_name: str
    class_name: str | None
    raw_selector: str

    @property
    def qualified_name(self) -> str:
        """Return the fully qualified function or method name (e.g. Class.method or func)."""
        if self.class_name:
            return f"{self.class_name}.{self.function_name}"
        return self.function_name


def parse_function_selector(selector: str) -> FunctionSelector:
    """Parse and validate a function selector string.

    Args:
        selector: String in format `[file.py::][ClassName.]function_name`

    Returns:
        Validated FunctionSelector instance.

    Raises:
        MalformedSelectorError: If syntax is invalid, empty, or targets nested functions.
    """
    raw = selector.strip()
    if not raw:
        raise MalformedSelectorError("Function selector cannot be empty")

    if "::" in raw:
        parts = raw.split("::")
        if len(parts) > 2:
            raise MalformedSelectorError(
                f"Malformed selector '{selector}': multiple '::' delimiters are not allowed"
            )
        file_part, func_part = parts[0].strip(), parts[1].strip()
        if not file_part:
            raise MalformedSelectorError(
                f"Malformed selector '{selector}': missing target file path before '::'"
            )
        if not func_part:
            raise MalformedSelectorError(
                f"Malformed selector '{selector}': missing function name after '::'"
            )
    else:
        file_part = None
        func_part = raw

    # Validate function identifier part
    ident_parts = func_part.split(".")
    for p in ident_parts:
        if not p.isidentifier():
            raise MalformedSelectorError(
                f"Malformed selector '{selector}': '{p}' is not a valid Python identifier"
            )

    if len(ident_parts) > 1:
        class_name: str | None = ".".join(ident_parts[:-1])
        func_name = ident_parts[-1]
    else:
        class_name = None
        func_name = ident_parts[0]

    return FunctionSelector(
        file_path=file_part,
        function_name=func_name,
        class_name=class_name,
        raw_selector=raw,
    )


def match_function_selector(
    selector: FunctionSelector | str,
    ast_facts: ASTFacts,
    target_path: str | None = None,
) -> ASTFunctionFact | None:
    """Match a parsed or raw selector against extracted AST facts.

    Returns:
        Matched ASTFunctionFact or None if not found.

    Raises:
        MalformedSelectorError: If selector string is malformed, targets a nested function, or is ambiguous.
    """
    if isinstance(selector, str):
        parsed = parse_function_selector(selector)
    else:
        parsed = selector

    # Check if selector attempts to target a nested function inside a function or method
    if parsed.class_name is not None:
        top_level_func_names = {f.name for f in ast_facts.functions if not f.is_method}
        method_qual_names = {f.qualified_name for f in ast_facts.functions if f.is_method}
        segments = parsed.class_name.split(".")
        for i in range(1, len(segments) + 1):
            prefix = ".".join(segments[:i])
            if prefix in top_level_func_names or prefix in method_qual_names:
                raise MalformedSelectorError(
                    f"Malformed selector '{parsed.raw_selector}': nested functions are not supported in selectors. "
                    "Only top-level functions and class methods can be selected."
                )

    # If selector specifies a file path, verify it matches target_path
    if parsed.file_path is not None and target_path is not None:
        sel_name = Path(parsed.file_path).name.lower()
        tgt_name = Path(target_path).name.lower()
        if sel_name != tgt_name:
            return None

    # Exact qualified name match
    if parsed.class_name is not None:
        target_qualified = f"{parsed.class_name}.{parsed.function_name}"
        for func in ast_facts.functions:
            if func.qualified_name == target_qualified:
                return func
        return None

    # Search top-level functions first
    for func in ast_facts.functions:
        if not func.is_method and func.name == parsed.function_name:
            return func

    # Search methods if no top-level function matched
    matching_methods = [
        func for func in ast_facts.functions if func.is_method and func.name == parsed.function_name
    ]
    if len(matching_methods) == 1:
        return matching_methods[0]
    if len(matching_methods) > 1:
        candidates = [m.qualified_name for m in matching_methods]
        raise MalformedSelectorError(
            f"Ambiguous selector '{parsed.function_name}' matches multiple methods: {candidates}. "
            f"Please qualify with ClassName.{parsed.function_name}."
        )

    return None


def resolve_function_selector(
    selector: str | FunctionSelector,
    ast_facts: ASTFacts,
    target_path: str | None = None,
) -> ASTFunctionFact:
    """Resolve a selector to a concrete ASTFunctionFact or raise SelectorNotFoundError.

    Args:
        selector: Raw selector string or parsed FunctionSelector.
        ast_facts: AST facts extracted from the target file.
        target_path: Optional path of the target file being analysed.

    Returns:
        The matched ASTFunctionFact.

    Raises:
        SelectorNotFoundError: If no matching function/method exists in ast_facts.
        MalformedSelectorError: If selector syntax is invalid or ambiguous.
    """
    matched = match_function_selector(selector, ast_facts, target_path=target_path)
    if matched is not None:
        return matched

    available = [f.qualified_name for f in ast_facts.functions]
    sel_text = selector.raw_selector if isinstance(selector, FunctionSelector) else selector
    raise SelectorNotFoundError(
        f"Function selector '{sel_text}' not found in target. Available functions: {available}",
        available_selectors=available,
    )


def resolve_selector_from_file(
    target_path: Path | str,
    selector: str | FunctionSelector,
) -> ASTFunctionFact:
    """Convenience helper to extract AST facts and resolve a selector directly from a file.

    Args:
        target_path: Path to Python source file.
        selector: Function selector string or parsed FunctionSelector.

    Returns:
        The matched ASTFunctionFact.
    """
    from localdev.languages.python.ast_analyser import extract_ast_facts_from_source

    path = Path(target_path).resolve()
    source = path.read_text(encoding="utf-8", errors="replace")
    facts = extract_ast_facts_from_source(source, filename=path.name)
    return resolve_function_selector(selector, facts, target_path=str(path))


def resolve_callable_from_module(
    module: Any,
    selector: str | FunctionSelector,
    target_path: str | None = None,
) -> tuple[Callable[..., Any], str, bool]:
    """Resolve a callable function or class method directly from an imported Python module object.

    Args:
        module: Live Python module object.
        selector: Function selector string or parsed FunctionSelector.
        target_path: Optional target file path to verify against selector's file part.

    Returns:
        Tuple of (callable_object, qualified_name, is_method).

    Raises:
        MalformedSelectorError: If selector syntax is invalid, ambiguous, or targets a nested function.
        SelectorNotFoundError: If the target function, class, or method cannot be found in the module.
    """
    if isinstance(selector, str):
        parsed = parse_function_selector(selector)
    else:
        parsed = selector

    # If selector specifies a file path, verify it matches target_path
    if parsed.file_path is not None and target_path is not None:
        sel_name = Path(parsed.file_path).name.lower()
        tgt_name = Path(target_path).name.lower()
        if sel_name != tgt_name:
            raise SelectorNotFoundError(
                f"Selector file '{parsed.file_path}' does not match target file '{Path(target_path).name}'."
            )

    # Check for nested function attempts
    if parsed.class_name is not None:
        segments = parsed.class_name.split(".")
        curr_obj = module
        for segment in segments:
            if hasattr(curr_obj, segment):
                sub = getattr(curr_obj, segment)
                if inspect.isfunction(sub) or inspect.isroutine(sub):
                    raise MalformedSelectorError(
                        f"Malformed selector '{parsed.raw_selector}': nested functions are not supported in selectors. "
                        "Only top-level functions and class methods can be selected."
                    )
                curr_obj = sub
            else:
                break

    # Case 1: Simple function / method name without class qualifier
    if parsed.class_name is None:
        # Search top-level functions first
        if hasattr(module, parsed.function_name):
            cand = getattr(module, parsed.function_name)
            if callable(cand) and not inspect.isclass(cand):
                return cand, parsed.function_name, False

        # Search methods in classes defined in this module
        matching_methods: list[tuple[type, Any, str]] = []
        for _, val in inspect.getmembers(module, inspect.isclass):
            if getattr(val, "__module__", None) in (module.__name__, None) and hasattr(val, parsed.function_name):
                m = getattr(val, parsed.function_name)
                if callable(m):
                    matching_methods.append((val, m, f"{val.__name__}.{parsed.function_name}"))

        if len(matching_methods) == 1:
            _cls, method, qualname = matching_methods[0]
            return method, qualname, True
        if len(matching_methods) > 1:
            candidates = [q for _, _, q in matching_methods]
            raise MalformedSelectorError(
                f"Ambiguous selector '{parsed.function_name}' matches multiple methods: {candidates}. "
                f"Please qualify with ClassName.{parsed.function_name}."
            )

        # Collect available callables for error message
        available: list[str] = [
            k
            for k, v in module.__dict__.items()
            if callable(v) and not inspect.isclass(v) and not k.startswith("_")
        ]
        for _, val in inspect.getmembers(module, inspect.isclass):
            if getattr(val, "__module__", None) in (module.__name__, None):
                for mk, mv in val.__dict__.items():
                    if callable(mv) and not mk.startswith("_"):
                        available.append(f"{val.__name__}.{mk}")

        raise SelectorNotFoundError(
            f"Function selector '{parsed.raw_selector}' not found in target module. Available functions: {available}",
            available_selectors=available,
        )

    # Case 2: Class-qualified method
    segments = parsed.class_name.split(".")
    curr_target = module
    for segment in segments:
        if not hasattr(curr_target, segment):
            raise SelectorNotFoundError(
                f"Class or container '{segment}' in selector '{parsed.raw_selector}' not found in target module."
            )
        curr_target = getattr(curr_target, segment)
        if inspect.isfunction(curr_target) or inspect.isroutine(curr_target):
            raise MalformedSelectorError(
                f"Malformed selector '{parsed.raw_selector}': nested functions are not supported in selectors. "
                "Only top-level functions and class methods can be selected."
            )

    if not inspect.isclass(curr_target):
        raise SelectorNotFoundError(
            f"Target container '{parsed.class_name}' in selector '{parsed.raw_selector}' is not a class."
        )

    if not hasattr(curr_target, parsed.function_name):
        available_methods = [
            k for k, v in curr_target.__dict__.items() if callable(v) and not k.startswith("_")
        ]
        raise SelectorNotFoundError(
            f"Method '{parsed.function_name}' not found on class '{curr_target.__name__}'. "
            f"Available methods: {available_methods}",
            available_selectors=[f"{curr_target.__name__}.{m}" for m in available_methods],
        )

    method_obj = getattr(curr_target, parsed.function_name)
    if not callable(method_obj):
        raise SelectorNotFoundError(
            f"Attribute '{parsed.function_name}' on class '{curr_target.__name__}' is not callable."
        )

    return method_obj, f"{parsed.class_name}.{parsed.function_name}", True

