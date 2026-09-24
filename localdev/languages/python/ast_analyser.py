"""Python AST fact extraction and structural analysis.

Parses valid Python AST to extract module-level functions, classes, methods,
parameters, loops, branches, calls, returns, and precise 1-based line spans
for context building, complexity analysis, and selector matching.

Short-circuits safely on syntax errors without raising or traversing AST.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.languages.python.syntax import decode_source
from localdev.schemas import ASTClassFact, ASTFacts, ASTFunctionFact

if TYPE_CHECKING:
    from localdev.schemas import TargetRecord


@dataclass(frozen=True)
class LoopFact:
    """Structural metadata for an iteration loop inside a function."""

    loop_type: str  # "for", "async_for", "while"
    start_line: int
    end_line: int
    nesting_depth: int
    target: str = ""
    iter_expr: str = ""


@dataclass(frozen=True)
class BranchFact:
    """Structural metadata for a branch inside a function."""

    branch_type: str  # "if", "if_exp", "match", "try"
    start_line: int
    end_line: int
    has_else: bool = False


@dataclass(frozen=True)
class CallFact:
    """Metadata for a function call inside a function body."""

    callee: str
    start_line: int
    arg_count: int
    is_recursive: bool = False


@dataclass(frozen=True)
class ReturnFact:
    """Metadata for a return statement inside a function body."""

    start_line: int
    has_value: bool = False


@dataclass(frozen=True)
class FunctionStructure:
    """In-depth structural breakdown of a function or method body."""

    name: str
    qualified_name: str
    start_line: int
    end_line: int
    parameters: list[str] = field(default_factory=list)
    docstring: str | None = None
    is_async: bool = False
    is_method: bool = False
    loops: list[LoopFact] = field(default_factory=list)
    max_loop_depth: int = 0
    branches: list[BranchFact] = field(default_factory=list)
    calls: list[CallFact] = field(default_factory=list)
    returns: list[ReturnFact] = field(default_factory=list)
    comprehensions: list[str] = field(default_factory=list)
    has_recursion: bool = False
    nested_functions: list[str] = field(default_factory=list)


def _get_node_start_line(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    """Get the earliest 1-based start line of a definition, including decorators."""
    if node.decorator_list:
        decorator_lines = [
            d.lineno for d in node.decorator_list if getattr(d, "lineno", None) is not None
        ]
        if decorator_lines:
            return min(min(decorator_lines), node.lineno)
    return node.lineno


def _get_node_end_line(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    fallback_start: int,
) -> int:
    """Get the 1-based end line of a definition."""
    end = getattr(node, "end_lineno", None)
    if end is not None and isinstance(end, int):
        return max(fallback_start, end)
    return fallback_start


def _extract_parameters(args_node: ast.arguments) -> list[str]:
    """Extract list of parameter names from an ast.arguments node in declaration order."""
    params: list[str] = []
    # Positional-only args
    for arg in args_node.posonlyargs:
        params.append(arg.arg)
    # Regular positional args
    for arg in args_node.args:
        params.append(arg.arg)
    # Vararg (*args)
    if args_node.vararg:
        params.append(f"*{args_node.vararg.arg}")
    # Keyword-only args
    for arg in args_node.kwonlyargs:
        params.append(arg.arg)
    # Kwarg (**kwargs)
    if args_node.kwarg:
        params.append(f"**{args_node.kwarg.arg}")
    return params


def _get_callee_name(node: ast.expr) -> str:
    """Extract a dotted or simple name for a call target expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _get_callee_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def analyse_function_structure(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_name: str | None = None,
) -> FunctionStructure:
    """Extract in-depth structural facts (loops, branches, calls, returns) from a function AST."""
    func_name = node.name
    qualified_name = f"{class_name}.{func_name}" if class_name else func_name
    start_line = max(1, _get_node_start_line(node))
    end_line = max(start_line, _get_node_end_line(node, start_line))
    params = _extract_parameters(node.args)
    doc = ast.get_docstring(node)
    is_async = isinstance(node, ast.AsyncFunctionDef)
    is_method = class_name is not None

    loops: list[LoopFact] = []
    branches: list[BranchFact] = []
    calls: list[CallFact] = []
    returns: list[ReturnFact] = []
    comprehensions: list[str] = []
    nested_functions: list[str] = []
    max_depth = 0
    has_recursion = False

    class FunctionBodyVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.current_loop_depth = 0

        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
            if child is not node:
                nested_functions.append(child.name)
            self.generic_visit(child)

        def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:
            if child is not node:
                nested_functions.append(child.name)
            self.generic_visit(child)

        def visit_For(self, child: ast.For) -> None:
            self.current_loop_depth += 1
            nonlocal max_depth
            max_depth = max(max_depth, self.current_loop_depth)
            loops.append(
                LoopFact(
                    loop_type="for",
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    nesting_depth=self.current_loop_depth,
                    target=ast.unparse(child.target) if hasattr(ast, "unparse") else "",
                    iter_expr=ast.unparse(child.iter) if hasattr(ast, "unparse") else "",
                )
            )
            self.generic_visit(child)
            self.current_loop_depth -= 1

        def visit_AsyncFor(self, child: ast.AsyncFor) -> None:
            self.current_loop_depth += 1
            nonlocal max_depth
            max_depth = max(max_depth, self.current_loop_depth)
            loops.append(
                LoopFact(
                    loop_type="async_for",
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    nesting_depth=self.current_loop_depth,
                    target=ast.unparse(child.target) if hasattr(ast, "unparse") else "",
                    iter_expr=ast.unparse(child.iter) if hasattr(ast, "unparse") else "",
                )
            )
            self.generic_visit(child)
            self.current_loop_depth -= 1

        def visit_While(self, child: ast.While) -> None:
            self.current_loop_depth += 1
            nonlocal max_depth
            max_depth = max(max_depth, self.current_loop_depth)
            loops.append(
                LoopFact(
                    loop_type="while",
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    nesting_depth=self.current_loop_depth,
                    iter_expr=ast.unparse(child.test) if hasattr(ast, "unparse") else "",
                )
            )
            self.generic_visit(child)
            self.current_loop_depth -= 1

        def visit_If(self, child: ast.If) -> None:
            branches.append(
                BranchFact(
                    branch_type="if",
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    has_else=bool(child.orelse),
                )
            )
            self.generic_visit(child)

        def visit_IfExp(self, child: ast.IfExp) -> None:
            branches.append(
                BranchFact(
                    branch_type="if_exp",
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    has_else=True,
                )
            )
            self.generic_visit(child)

        def visit_Match(self, child: ast.Match) -> None:
            branches.append(
                BranchFact(
                    branch_type="match",
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    has_else=False,
                )
            )
            self.generic_visit(child)

        def visit_Try(self, child: ast.Try) -> None:
            branches.append(
                BranchFact(
                    branch_type="try",
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    has_else=bool(child.orelse),
                )
            )
            self.generic_visit(child)

        def visit_Call(self, child: ast.Call) -> None:
            callee_name = _get_callee_name(child.func)
            nonlocal has_recursion
            is_rec = False
            if callee_name in (func_name, qualified_name, f"self.{func_name}"):
                is_rec = True
                has_recursion = True
            calls.append(
                CallFact(
                    callee=callee_name,
                    start_line=child.lineno,
                    arg_count=len(child.args) + len(child.keywords),
                    is_recursive=is_rec,
                )
            )
            self.generic_visit(child)

        def visit_Return(self, child: ast.Return) -> None:
            returns.append(
                ReturnFact(
                    start_line=child.lineno,
                    has_value=child.value is not None,
                )
            )
            self.generic_visit(child)

        def visit_ListComp(self, child: ast.ListComp) -> None:
            comprehensions.append("ListComp")
            self.generic_visit(child)

        def visit_SetComp(self, child: ast.SetComp) -> None:
            comprehensions.append("SetComp")
            self.generic_visit(child)

        def visit_DictComp(self, child: ast.DictComp) -> None:
            comprehensions.append("DictComp")
            self.generic_visit(child)

        def visit_GeneratorExp(self, child: ast.GeneratorExp) -> None:
            comprehensions.append("GeneratorExp")
            self.generic_visit(child)

    visitor = FunctionBodyVisitor()
    visitor.visit(node)

    return FunctionStructure(
        name=func_name,
        qualified_name=qualified_name,
        start_line=start_line,
        end_line=end_line,
        parameters=params,
        docstring=doc,
        is_async=is_async,
        is_method=is_method,
        loops=loops,
        max_loop_depth=max_depth,
        branches=branches,
        calls=calls,
        returns=returns,
        comprehensions=comprehensions,
        has_recursion=has_recursion,
        nested_functions=nested_functions,
    )


def extract_ast_facts_from_source(
    source: str | bytes,
    filename: str = "<string>",
) -> ASTFacts:
    """Extract AST facts from Python source code, short-circuiting on syntax error.

    Guarantees:
    - Never executes or imports target source code.
    - Captures SyntaxError and IndentationError safely without throwing.
    - Computes exact 1-based line ranges bounded within file length.
    - Resolves qualified names for class methods and nested classes.
    """
    if isinstance(source, bytes):
        source_text, _, _ = decode_source(source)
    else:
        source_text = source

    total_lines = len(source_text.splitlines()) if source_text else 0

    try:
        tree = ast.parse(source_text, filename=filename, mode="exec")
    except (SyntaxError, IndentationError) as err:
        return ASTFacts(
            functions=[],
            classes=[],
            total_lines=total_lines,
            syntax_error=str(err),
        )

    functions: list[ASTFunctionFact] = []
    classes: list[ASTClassFact] = []

    def process_class_def(class_node: ast.ClassDef, prefix: str = "") -> None:
        class_name = f"{prefix}{class_node.name}" if prefix else class_node.name
        c_start = max(1, _get_node_start_line(class_node))
        c_end = max(c_start, _get_node_end_line(class_node, c_start))
        if total_lines > 0:
            c_end = min(total_lines, c_end)

        method_names: list[str] = []

        for sub in class_node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_names.append(sub.name)
                m_start = max(1, _get_node_start_line(sub))
                m_end = max(m_start, _get_node_end_line(sub, m_start))
                if total_lines > 0:
                    m_end = min(total_lines, m_end)

                functions.append(
                    ASTFunctionFact(
                        name=sub.name,
                        qualified_name=f"{class_name}.{sub.name}",
                        start_line=m_start,
                        end_line=m_end,
                        parameters=_extract_parameters(sub.args),
                        is_async=isinstance(sub, ast.AsyncFunctionDef),
                        is_method=True,
                        docstring=ast.get_docstring(sub),
                    )
                )
            elif isinstance(sub, ast.ClassDef):
                # Nested class inside class
                process_class_def(sub, prefix=f"{class_name}.")

        classes.append(
            ASTClassFact(
                name=class_name,
                start_line=c_start,
                end_line=c_end,
                methods=method_names,
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f_start = max(1, _get_node_start_line(node))
            f_end = max(f_start, _get_node_end_line(node, f_start))
            if total_lines > 0:
                f_end = min(total_lines, f_end)

            functions.append(
                ASTFunctionFact(
                    name=node.name,
                    qualified_name=node.name,
                    start_line=f_start,
                    end_line=f_end,
                    parameters=_extract_parameters(node.args),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    is_method=False,
                    docstring=ast.get_docstring(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            process_class_def(node)

    return ASTFacts(
        functions=functions,
        classes=classes,
        total_lines=total_lines,
        syntax_error=None,
    )


def extract_ast_facts(
    target: TargetRecord,
    source_text: str | None = None,
) -> ASTFacts:
    """Extract AST facts for a validated TargetRecord."""
    if source_text is not None:
        return extract_ast_facts_from_source(source_text, filename=target.path)

    target_path = Path(target.absolute_path)
    if not target_path.is_file():
        return ASTFacts(functions=[], classes=[], total_lines=0, syntax_error="Target file not found")

    try:
        raw_bytes = target_path.read_bytes()
    except OSError as exc:
        return ASTFacts(
            functions=[],
            classes=[],
            total_lines=0,
            syntax_error=f"Failed to read target file: {exc}",
        )

    return extract_ast_facts_from_source(raw_bytes, filename=target.path)
