"""Function selector parsing, validation, and AST matching for Python targets.

Implements the contract:
- file.py::function_name
- file.py::ClassName.method_name
- function_name
- ClassName.method_name

Rejects malformed selectors and explicitly disallows nested functions in selectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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

    # Check if selector attempts to target a nested function inside a function
    if parsed.class_name is not None:
        top_level_func_names = {f.name for f in ast_facts.functions if not f.is_method}
        first_segment = parsed.class_name.split(".")[0]
        if first_segment in top_level_func_names:
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
