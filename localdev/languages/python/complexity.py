"""Restricted static complexity cost model grounded in CPython runtime semantics.

Enforces Phase 10 Task P10-T1 requirements:
1. Closed asymptotic vocabulary: O(1), O(log n), O(n), O(n log n), O(n²), O(n³), O(nm), O(2^n), UNKNOWN.
   Arbitrary complexity strings are strictly prohibited.
2. CPython 3.11/3.12 runtime semantics foundation:
   - Explicit distinction of amortized costs (e.g. list.append()).
   - Explicit distinction of expected/average costs under documented uniform hashing assumptions (dict/set).
   - Worst-case bounds for deterministic loops and sequences.
3. Formal space definitions:
   - Auxiliary space: transient intermediate memory (stack, heap buffers, slicing copies).
   - Output space: memory escaping execution as return values (generators/iterators are O(1)).
4. Mandatory abstention policy:
   - Unknown/unresolved function calls -> UNKNOWN_CALL.
   - Dynamic/unresolved loop bounds -> DYNAMIC_BOUNDS.
   - Dynamic/mutual/unbounded recursion -> DYNAMIC_RECURSION.
   - External library calls -> EXTERNAL_DEPENDENCY.
   - Unsupported or unparseable syntax -> UNSUPPORTED_SYNTAX.
5. Strict conservative + assumption-linked + source-linked + abstention-first contract.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from localdev.languages.python.ast_analyser import extract_ast_facts_from_source
from localdev.languages.python.selectors import resolve_function_selector
from localdev.schemas import (
    ComplexityAbstentionReason,
    ComplexityClassEnum,
    ComplexityReport,
    ConfidenceEnum,
    TargetRecord,
)

# =============================================================================
# Asymptotic Dominance Ordering & Closed Vocabulary
# =============================================================================

# Dominance ranks for single-variable polynomial and logarithmic classes
_DOMINANCE_RANK: dict[ComplexityClassEnum, int] = {
    ComplexityClassEnum.O_1: 0,
    ComplexityClassEnum.O_LOG_N: 1,
    ComplexityClassEnum.O_N: 2,
    ComplexityClassEnum.O_N_LOG_N: 3,
    ComplexityClassEnum.O_N2: 4,
    ComplexityClassEnum.O_N3: 5,
    ComplexityClassEnum.O_2N: 6,
}


def combine_addition(
    c1: ComplexityClassEnum,
    c2: ComplexityClassEnum,
) -> ComplexityClassEnum:
    """Return dominant asymptotic term of c1 + c2 under Big-O rules.

    If either operand is UNKNOWN, the sum is UNKNOWN (conservative abstention).
    """
    if c1 == ComplexityClassEnum.UNKNOWN or c2 == ComplexityClassEnum.UNKNOWN:
        return ComplexityClassEnum.UNKNOWN

    if c1 == c2:
        return c1

    if c1 == ComplexityClassEnum.O_1:
        return c2
    if c2 == ComplexityClassEnum.O_1:
        return c1

    # Special handling for bilinear O(nm)
    if c1 == ComplexityClassEnum.O_NM:
        if c2 in (ComplexityClassEnum.O_1, ComplexityClassEnum.O_LOG_N, ComplexityClassEnum.O_N):
            return ComplexityClassEnum.O_NM
        # If combined with quadratic or higher, defer to higher or UNKNOWN
        rank2 = _DOMINANCE_RANK.get(c2, -1)
        if rank2 >= _DOMINANCE_RANK[ComplexityClassEnum.O_N2]:
            return c2
        return ComplexityClassEnum.UNKNOWN

    if c2 == ComplexityClassEnum.O_NM:
        if c1 in (ComplexityClassEnum.O_1, ComplexityClassEnum.O_LOG_N, ComplexityClassEnum.O_N):
            return ComplexityClassEnum.O_NM
        rank1 = _DOMINANCE_RANK.get(c1, -1)
        if rank1 >= _DOMINANCE_RANK[ComplexityClassEnum.O_N2]:
            return c1
        return ComplexityClassEnum.UNKNOWN

    r1 = _DOMINANCE_RANK.get(c1)
    r2 = _DOMINANCE_RANK.get(c2)
    if r1 is not None and r2 is not None:
        return c1 if r1 >= r2 else c2

    return ComplexityClassEnum.UNKNOWN


def combine_multiplication(
    outer: ComplexityClassEnum,
    inner: ComplexityClassEnum,
    distinct_dimensions: bool = False,
) -> ComplexityClassEnum:
    """Multiply outer loop iteration count by inner body cost.

    If result exceeds O(n³) or falls outside the closed vocabulary, routes
    conservatively to UNKNOWN.
    """
    if outer == ComplexityClassEnum.UNKNOWN or inner == ComplexityClassEnum.UNKNOWN:
        return ComplexityClassEnum.UNKNOWN

    if outer == ComplexityClassEnum.O_1:
        return inner
    if inner == ComplexityClassEnum.O_1:
        return outer

    # O(n) * O(log n) = O(n log n)
    if (outer == ComplexityClassEnum.O_N and inner == ComplexityClassEnum.O_LOG_N) or (
        outer == ComplexityClassEnum.O_LOG_N and inner == ComplexityClassEnum.O_N
    ):
        return ComplexityClassEnum.O_N_LOG_N

    # O(n) * O(n) = O(nm) if distinct, else O(n²)
    if outer == ComplexityClassEnum.O_N and inner == ComplexityClassEnum.O_N:
        if distinct_dimensions:
            return ComplexityClassEnum.O_NM
        return ComplexityClassEnum.O_N2

    # O(n) * O(n²) = O(n³)
    if (outer == ComplexityClassEnum.O_N and inner == ComplexityClassEnum.O_N2) or (
        outer == ComplexityClassEnum.O_N2 and inner == ComplexityClassEnum.O_N
    ):
        return ComplexityClassEnum.O_N3

    # O(n) * O(m) = O(nm)
    if outer == ComplexityClassEnum.O_N and inner == ComplexityClassEnum.O_NM:
        return ComplexityClassEnum.UNKNOWN  # Exceeds closed vocabulary O(n²m)

    # Any higher combinations exceed closed vocabulary
    return ComplexityClassEnum.UNKNOWN


# =============================================================================
# CPython Operation Cost Table
# =============================================================================


@dataclass(frozen=True)
class OperationCost:
    """Formal asymptotic cost specification for a CPython built-in operation."""

    time: ComplexityClassEnum
    aux_space: ComplexityClassEnum
    is_amortized: bool = False
    is_expected: bool = False
    assumption: str | None = None
    description: str = ""


# CPython 3.11/3.12 Built-in Function Costs
BUILTIN_FUNCTION_COSTS: dict[str, OperationCost] = {
    "len": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="len() reads PyVarObject.ob_size directly in O(1) worst-case time.",
    ),
    "range": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="range() instantiates a lazy range object in O(1) time and space.",
    ),
    "enumerate": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="enumerate() instantiates a lazy generator/iterator wrapper in O(1) space.",
    ),
    "zip": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="zip() instantiates a lazy tuple iterator in O(1) space.",
    ),
    "iter": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="iter() instantiates a container iterator in O(1) space.",
    ),
    "reversed": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="reversed() returns a lazy reverse sequence iterator in O(1) space.",
    ),
    "abs": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Primitive absolute value operation.",
    ),
    "ord": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Primitive character codepoint lookup.",
    ),
    "chr": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Primitive unicode character generation.",
    ),
    "isinstance": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Type inspection in O(1) time.",
    ),
    "id": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Memory address inspection in O(1) time.",
    ),
    "type": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Object type pointer inspection in O(1) time.",
    ),
    "int": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Integer primitive conversion.",
    ),
    "float": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Float primitive conversion.",
    ),
    "bool": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="Boolean truth inspection in O(1) time.",
    ),
    "min": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="min() scans an iterable of length n in linear O(n) time.",
    ),
    "max": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="max() scans an iterable of length n in linear O(n) time.",
    ),
    "sum": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="sum() accumulates an iterable of length n in linear O(n) time.",
    ),
    "sorted": OperationCost(
        time=ComplexityClassEnum.O_N_LOG_N,
        aux_space=ComplexityClassEnum.O_N,
        description="sorted() performs Timsort/Powersort in O(n log n) time and O(n) auxiliary run buffers.",
    ),
    "list": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="list() instantiates an empty list in O(1) time.",
    ),
    "set": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="set() instantiates an empty set in O(1) time.",
    ),
    "dict": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="dict() instantiates an empty dictionary in O(1) time.",
    ),
    "tuple": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="tuple() instantiates an empty tuple in O(1) time.",
    ),
    "str": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="str() primitive string conversion in O(1) time.",
    ),
    "print": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="print() writes to output stream in O(1) time.",
    ),
    "any": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="any() scans iterable in O(n) worst-case time.",
    ),
    "all": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="all() scans iterable in O(n) worst-case time.",
    ),
}

# CPython Standard Sequence & Mapping Method Costs
STANDARD_METHOD_COSTS: dict[str, OperationCost] = {
    "append": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        is_amortized=True,
        assumption="Amortized O(1) due to CPython dynamic array geometric over-allocation.",
        description="list.append() appends in amortized O(1) time.",
    ),
    "pop": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        is_amortized=False,
        description="list.pop() from end removes in O(1) time without pointer shifting.",
    ),
    "sort": OperationCost(
        time=ComplexityClassEnum.O_N_LOG_N,
        aux_space=ComplexityClassEnum.O_N,
        description="list.sort() in-place Timsort/Powersort in O(n log n) time and O(n) run buffers.",
    ),
    "reverse": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="list.reverse() in-place two-pointer swap in O(n) time and O(1) space.",
    ),
    "insert": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="list.insert() shifts subsequent pointers in O(n) linear time.",
    ),
    "remove": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="list.remove() searches and shifts pointers in O(n) linear time.",
    ),
    "index": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="list.index() searches linearly in O(n) worst-case time.",
    ),
    "count": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        description="list.count() scans sequence in O(n) linear time.",
    ),
    "extend": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        is_amortized=True,
        assumption="Amortized O(1) per appended element in dynamic array.",
        description="list.extend() appends m elements in linear time.",
    ),
    "copy": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_N,
        description="Container shallow copy creates O(n) transient elements.",
    ),
    "update": OperationCost(
        time=ComplexityClassEnum.O_N,
        aux_space=ComplexityClassEnum.O_1,
        is_expected=True,
        assumption="Assumes uniform hash distribution without pathological collisions.",
        description="dict/set.update() in expected O(n) time.",
    ),
    "get": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        is_expected=True,
        assumption="Assumes uniform hash distribution without pathological collisions.",
        description="dict.get() lookup in expected O(1) time.",
    ),
    "keys": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="dict.keys() returns an O(1) view object.",
    ),
    "values": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="dict.values() returns an O(1) view object.",
    ),
    "items": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        description="dict.items() returns an O(1) view object.",
    ),
    "add": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        is_expected=True,
        assumption="Assumes uniform hash distribution without pathological collisions.",
        description="set.add() in expected O(1) time.",
    ),
    "discard": OperationCost(
        time=ComplexityClassEnum.O_1,
        aux_space=ComplexityClassEnum.O_1,
        is_expected=True,
        assumption="Assumes uniform hash distribution without pathological collisions.",
        description="set.discard() in expected O(1) time.",
    ),
}


# =============================================================================
# Abstention Helper
# =============================================================================


def create_abstention_report(
    target: str,
    reason: ComplexityAbstentionReason,
    details: str,
    start_line: int | None = None,
    end_line: int | None = None,
    assumptions: list[str] | None = None,
) -> ComplexityReport:
    """Construct a clean, schema-valid abstention ComplexityReport with UNKNOWN."""
    return ComplexityReport(
        target=target,
        time_complexity=ComplexityClassEnum.UNKNOWN,
        auxiliary_space=ComplexityClassEnum.UNKNOWN,
        output_space=ComplexityClassEnum.UNKNOWN,
        confidence=ConfidenceEnum.LOW,
        is_amortized=False,
        is_expected=False,
        assumptions=assumptions or [],
        abstention_reason=reason,
        details=details,
        start_line=start_line,
        end_line=end_line,
    )


# =============================================================================
# AST Complexity Classifier & Cost Model Engine
# =============================================================================


class ContainerKind(str, Enum):
    """Categorization of containers for static complexity analysis."""

    LIST = "list"
    SET = "set"
    DICT = "dict"
    GENERATOR = "generator"
    OTHER = "other"


class CostModelAnalyzer:
    """Evaluates Python AST definitions against the formal CPython cost model."""

    def __init__(self, target_name: str, source_text: str) -> None:
        self.target_name = target_name
        self.source_text = source_text
        self.source_lines = source_text.splitlines()
        self.tree: ast.Module | None = None

    def analyze_selector(self, selector: str | None = None) -> ComplexityReport:
        """Parse source text and evaluate complexity for specified function selector."""
        try:
            tree = ast.parse(self.source_text)
            self.tree = tree
        except SyntaxError as exc:
            return create_abstention_report(
                target=self._format_target(selector),
                reason=ComplexityAbstentionReason.UNSUPPORTED_SYNTAX,
                details=f"Target file contains invalid Python syntax: {exc}",
                start_line=exc.lineno or 1,
            )

        # If selector provided, find specific function / method
        if selector:
            fn_node, qualified_name = self._find_function_node(tree, selector)
            if fn_node is None:
                return create_abstention_report(
                    target=self._format_target(selector),
                    reason=ComplexityAbstentionReason.UNSUPPORTED_SYNTAX,
                    details=f"Function selector '{selector}' was not found in AST.",
                )
            return self.analyze_function(fn_node, qualified_name)

        # If no selector, inspect first function or file-level code
        first_fn = next(
            (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
            None,
        )
        if first_fn is not None:
            return self.analyze_function(first_fn, first_fn.name)

        # File-level statements analysis
        return self._analyze_module_body(tree.body)

    def analyze_all(self) -> list[ComplexityReport]:
        """Parse source text and evaluate complexity for all functions/methods or file body."""
        try:
            tree = ast.parse(self.source_text)
            self.tree = tree
        except SyntaxError as exc:
            return [
                create_abstention_report(
                    target=self.target_name,
                    reason=ComplexityAbstentionReason.UNSUPPORTED_SYNTAX,
                    details=f"Target file contains invalid Python syntax: {exc}",
                    start_line=exc.lineno or 1,
                )
            ]

        reports: list[ComplexityReport] = []

        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                reports.append(self.analyze_function(stmt, stmt.name))
            elif isinstance(stmt, ast.ClassDef):
                for item in stmt.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        reports.append(self.analyze_function(item, f"{stmt.name}.{item.name}"))

        if not reports:
            reports.append(self._analyze_module_body(tree.body))

        return reports

    def analyze_file(self) -> list[ComplexityReport]:
        """Alias for analyze_all."""
        return self.analyze_all()

    def analyze_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        function_name: str,
    ) -> ComplexityReport:
        """Analyze algorithmic complexity for a single function or method definition."""
        if self.tree is None:
            try:
                self.tree = ast.parse(self.source_text)
            except SyntaxError:
                pass

        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line)
        target_str = self._format_target(function_name)

        # 1. Check for unsupported top-level async / generator complexity in MVP
        # If function contains 'yield', output space is O(1) lazy generator
        is_generator = any(
            isinstance(sub, (ast.Yield, ast.YieldFrom)) for sub in ast.walk(node)
        )

        # 2. Check for unknown function or method calls, mutual recursion, external libraries
        abstention = self._check_unsupported_calls(
            node, target_str, start_line, end_line, current_func_name=node.name
        )
        if abstention is not None:
            return abstention

        # 3. Check for dynamic loop bounds (while loops or dynamic iterables)
        loop_abstention = self._check_dynamic_loops(node, target_str, start_line, end_line)
        if loop_abstention is not None:
            return loop_abstention

        # 4. Check for direct recursion patterns
        self_calls = self._collect_recursive_calls(node)
        if self_calls:
            return self._evaluate_recursion(node, self_calls, target_str, start_line, end_line)

        # 5. Evaluate iterative structure and operation costs
        return self._evaluate_function_costs(node, target_str, start_line, end_line, is_generator)

    def _format_target(self, selector: str | None) -> str:
        if not selector:
            return self.target_name
        if "::" in self.target_name:
            return self.target_name
        return f"{self.target_name}::{selector}"

    def _find_function_node(
        self,
        tree: ast.Module,
        selector: str,
    ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | None, str]:
        """Locate function or Class.method node in AST matching selector.

        Returns (node, qualified_name).
        Raises:
            MalformedSelectorError: If selector is ambiguous or targets nested functions.
            SelectorNotFoundError: If selector cannot be resolved.
        """
        facts = extract_ast_facts_from_source(self.source_text)
        fact = resolve_function_selector(selector, facts, target_path=self.target_name)
        for sub in ast.walk(tree):
            if (
                isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                and sub.lineno == fact.start_line
            ):
                return sub, fact.qualified_name
        return None, fact.qualified_name

    def _collect_recursive_calls(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> list[ast.Call]:
        """Collect all direct recursive self-calls in function."""
        calls: list[ast.Call] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and (
                (isinstance(sub.func, ast.Name) and sub.func.id == node.name)
                or (
                    isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == node.name
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id in ("self", "cls")
                )
            ):
                calls.append(sub)
        return calls

    def _is_linear_decrement_call(self, sc: ast.Call) -> bool:
        """Check if recursive call argument represents a linear decrement or progression."""
        for arg in sc.args:
            if isinstance(arg, ast.BinOp):
                if (
                    isinstance(arg.left, (ast.Name, ast.Call))
                    and isinstance(arg.op, ast.Sub)
                    and isinstance(arg.right, ast.Constant)
                    and isinstance(arg.right.value, int)
                ):
                    return True
                if (
                    isinstance(arg.left, ast.Name)
                    and isinstance(arg.op, ast.Add)
                    and isinstance(arg.right, ast.Constant)
                    and isinstance(arg.right.value, int)
                ):
                    return True
            elif isinstance(arg, ast.Subscript) and isinstance(arg.slice, ast.Slice):
                return True
        return False

    def _is_halving_call(
        self, sc: ast.Call, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> bool:
        """Check if recursive call argument represents a divide-and-conquer halving step."""
        halved_vars = {
            t.id
            for s in ast.walk(node)
            if isinstance(s, ast.Assign)
            for t in s.targets
            if isinstance(t, ast.Name)
            and isinstance(s.value, ast.BinOp)
            and isinstance(s.value.op, (ast.FloorDiv, ast.Div))
            and isinstance(s.value.right, ast.Constant)
            and s.value.right.value == 2
        }
        for arg in sc.args:
            # Direct division by 2: n // 2 or len(x) // 2
            if (
                isinstance(arg, ast.BinOp)
                and isinstance(arg.op, (ast.FloorDiv, ast.Div))
                and isinstance(arg.right, ast.Constant)
                and arg.right.value == 2
            ):
                return True
            # Binary search interval halving: mid, mid - 1, or mid + 1 where mid was computed via halving
            if isinstance(arg, ast.Name) and arg.id in halved_vars:
                return True
            if (
                isinstance(arg, ast.BinOp)
                and isinstance(arg.left, ast.Name)
                and arg.left.id in halved_vars
                and isinstance(arg.op, (ast.Add, ast.Sub))
                and isinstance(arg.right, ast.Constant)
            ):
                return True
        return False

    def _evaluate_recursion(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        self_calls: list[ast.Call],
        target_str: str,
        start_line: int,
        end_line: int,
    ) -> ComplexityReport:
        """Analyze algorithmic complexity and space for recursive algorithms."""
        # 1. Base case verification
        has_branching = any(isinstance(sub, (ast.If, ast.IfExp)) for sub in ast.walk(node))
        has_base_case = False

        if has_branching:
            for sub in ast.walk(node):
                if isinstance(sub, ast.If):
                    body_has_self = any(sc in ast.walk(s) for s in sub.body for sc in self_calls)
                    body_returns = any(isinstance(s, (ast.Return, ast.Raise)) for stmt in sub.body for s in ast.walk(stmt))
                    orelse_has_self = any(sc in ast.walk(s) for s in sub.orelse for sc in self_calls)
                    orelse_returns = any(isinstance(s, (ast.Return, ast.Raise)) for stmt in sub.orelse for s in ast.walk(stmt))

                    if body_returns and not body_has_self:
                        has_base_case = True
                        break
                    if orelse_returns and not orelse_has_self:
                        has_base_case = True
                        break
                    if sub.orelse and (not body_has_self or not orelse_has_self):
                        has_base_case = True
                        break

                elif isinstance(sub, ast.IfExp):
                    body_has_self = any(sc in ast.walk(sub.body) for sc in self_calls)
                    orelse_has_self = any(sc in ast.walk(sub.orelse) for sc in self_calls)
                    if (not body_has_self and orelse_has_self) or (body_has_self and not orelse_has_self):
                        has_base_case = True
                        break

            if not has_base_case:
                returns = [s for s in ast.walk(node) if isinstance(s, (ast.Return, ast.Raise))]
                non_recursive_returns = [
                    r for r in returns
                    if not any(sc in ast.walk(r) for sc in self_calls)
                ]
                if non_recursive_returns:
                    has_base_case = True

        if not has_base_case:
            return create_abstention_report(
                target=target_str,
                reason=ComplexityAbstentionReason.DYNAMIC_RECURSION,
                details=f"Recursive function '{node.name}' lacks a recognizable static base case.",
                start_line=start_line,
                end_line=end_line,
            )

        # 2. Check for recursion inside loop (dynamic branching recursion)
        for sub in ast.walk(node):
            if isinstance(sub, (ast.For, ast.While)):
                for sc in self_calls:
                    if sc in ast.walk(sub):
                        return create_abstention_report(
                            target=target_str,
                            reason=ComplexityAbstentionReason.DYNAMIC_RECURSION,
                            details=(
                                f"Encountered recursive call inside loop in '{node.name}'. "
                                "Dynamic branching recursion requires abstention."
                            ),
                            start_line=getattr(sc, "lineno", start_line),
                            end_line=end_line,
                        )

        # 3. Check for binary branching recursion (e.g. fib(n-1) + fib(n-2))
        is_binary_branching = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and sub.value is not None:
                sub_calls = [sc for sc in self_calls if sc in ast.walk(sub.value)]
                if len(sub_calls) >= 2:
                    is_binary_branching = True
                    break
            elif isinstance(sub, ast.BinOp):
                sub_calls = [sc for sc in self_calls if sc in ast.walk(sub)]
                if len(sub_calls) >= 2:
                    is_binary_branching = True
                    break

        if is_binary_branching:
            all_decrements = all(self._is_linear_decrement_call(sc) for sc in self_calls)
            if all_decrements:
                return ComplexityReport(
                    target=target_str,
                    time_complexity=ComplexityClassEnum.O_2N,
                    auxiliary_space=ComplexityClassEnum.O_N,
                    output_space=ComplexityClassEnum.O_1,
                    confidence=ConfidenceEnum.HIGH,
                    is_amortized=False,
                    is_expected=False,
                    assumptions=[
                        "Binary branching recursion without memoization yields O(2^n) time and O(n) auxiliary call-stack space."
                    ],
                    abstention_reason=None,
                    details=(
                        f"Derived {ComplexityClassEnum.O_2N.value} time complexity and "
                        f"{ComplexityClassEnum.O_N.value} auxiliary call-stack space under CPython 3.11/3.12 semantics."
                    ),
                    start_line=start_line,
                    end_line=end_line,
                )
            return create_abstention_report(
                target=target_str,
                reason=ComplexityAbstentionReason.DYNAMIC_RECURSION,
                details=f"Binary recursive call step in '{node.name}' contains data-dependent or dynamic arguments.",
                start_line=start_line,
                end_line=end_line,
            )

        # 4. Check for divide-and-conquer recursion (halving)
        if all(self._is_halving_call(sc, node) for sc in self_calls):
            return ComplexityReport(
                target=target_str,
                time_complexity=ComplexityClassEnum.O_LOG_N,
                auxiliary_space=ComplexityClassEnum.O_LOG_N,
                output_space=ComplexityClassEnum.O_1,
                confidence=ConfidenceEnum.HIGH,
                is_amortized=False,
                is_expected=False,
                assumptions=[
                    "Divide-and-conquer recursion with logarithmic depth yields O(log n) time and auxiliary call-stack space."
                ],
                abstention_reason=None,
                details=(
                    f"Derived {ComplexityClassEnum.O_LOG_N.value} time complexity and "
                    f"{ComplexityClassEnum.O_LOG_N.value} auxiliary call-stack space under CPython 3.11/3.12 semantics."
                ),
                start_line=start_line,
                end_line=end_line,
            )

        # 5. Check for direct linear recursion
        if all(self._is_linear_decrement_call(sc) for sc in self_calls):
            out_space = ComplexityClassEnum.O_1
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Return)
                    and sub.value is not None
                    and isinstance(sub.value, (ast.List, ast.ListComp))
                ):
                    out_space = ComplexityClassEnum.O_N

            return ComplexityReport(
                target=target_str,
                time_complexity=ComplexityClassEnum.O_N,
                auxiliary_space=ComplexityClassEnum.O_N,
                output_space=out_space,
                confidence=ConfidenceEnum.HIGH,
                is_amortized=False,
                is_expected=False,
                assumptions=[
                    "Direct linear recursion yields O(n) time and O(n) auxiliary call-stack space."
                ],
                abstention_reason=None,
                details=(
                    f"Derived {ComplexityClassEnum.O_N.value} time complexity and "
                    f"{ComplexityClassEnum.O_N.value} auxiliary call-stack space under CPython 3.11/3.12 semantics."
                ),
                start_line=start_line,
                end_line=end_line,
            )

        # 6. Dynamic or unrecognized recurrence step
        return create_abstention_report(
            target=target_str,
            reason=ComplexityAbstentionReason.DYNAMIC_RECURSION,
            details=f"Recursive call step in '{node.name}' contains data-dependent or dynamic arguments.",
            start_line=start_line,
            end_line=end_line,
        )

    def _check_unsupported_calls(
        self,
        node: ast.AST,
        target_str: str,
        start_line: int,
        end_line: int,
        current_func_name: str | None = None,
    ) -> ComplexityReport | None:
        """Scan AST for external calls, unknown user calls, mutual recursion, or dynamic dispatch."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                call_line = getattr(sub, "lineno", start_line)

                # Direct function call: name(...)
                if isinstance(sub.func, ast.Name):
                    fn_name = sub.func.id
                    if fn_name in BUILTIN_FUNCTION_COSTS:
                        continue
                    if current_func_name is not None and fn_name == current_func_name:
                        # Recursive self-call
                        continue
                    # Check for mutual recursion if AST tree is available
                    if self.tree is not None and current_func_name is not None:
                        target_fn = next(
                            (
                                s for s in self.tree.body
                                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and s.name == fn_name
                            ),
                            None,
                        )
                        if target_fn is not None:
                            # Check if target_fn calls current_func_name
                            calls_back = any(
                                isinstance(c, ast.Call)
                                and isinstance(c.func, ast.Name)
                                and c.func.id == current_func_name
                                for c in ast.walk(target_fn)
                            )
                            if calls_back:
                                return create_abstention_report(
                                    target=target_str,
                                    reason=ComplexityAbstentionReason.DYNAMIC_RECURSION,
                                    details=(
                                        f"Encountered mutual recursion between '{current_func_name}' and '{fn_name}'. "
                                        "Localdev strictly abstains on mutual recursion."
                                    ),
                                    start_line=call_line,
                                    end_line=end_line,
                                )

                    # Call to unknown function or user function
                    return create_abstention_report(
                        target=target_str,
                        reason=ComplexityAbstentionReason.UNKNOWN_CALL,
                        details=(
                            f"Encountered unknown or user-defined function call '{fn_name}()' at line {call_line}. "
                            "Localdev strictly abstains on unestablished function call costs."
                        ),
                        start_line=call_line,
                        end_line=end_line,
                    )

                # Attribute call: obj.method(...) or module.func(...)
                if isinstance(sub.func, ast.Attribute):
                    attr_name = sub.func.attr
                    if attr_name in STANDARD_METHOD_COSTS:
                        continue
                    if (
                        isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id in ("self", "cls")
                    ):
                        if current_func_name is not None and attr_name == current_func_name:
                            # Recursive method self-call
                            continue
                        if self.tree is not None and current_func_name is not None:
                            target_methods = [
                                m
                                for m in ast.walk(self.tree)
                                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and m.name == attr_name
                            ]
                            for tm in target_methods:
                                calls_back = any(
                                    isinstance(c, ast.Call)
                                    and (
                                        (isinstance(c.func, ast.Name) and c.func.id == current_func_name)
                                        or (
                                            isinstance(c.func, ast.Attribute)
                                            and c.func.attr == current_func_name
                                            and isinstance(c.func.value, ast.Name)
                                            and c.func.value.id in ("self", "cls")
                                        )
                                    )
                                    for c in ast.walk(tm)
                                )
                                if calls_back:
                                    return create_abstention_report(
                                        target=target_str,
                                        reason=ComplexityAbstentionReason.DYNAMIC_RECURSION,
                                        details=(
                                            f"Encountered mutual recursion between method '{current_func_name}' and '{attr_name}'. "
                                            "Localdev strictly abstains on mutual recursion."
                                        ),
                                        start_line=call_line,
                                        end_line=end_line,
                                    )
                    # Check if module call (e.g. math.sqrt, requests.get)
                    if isinstance(sub.func.value, ast.Name):
                        val_name = sub.func.value.id
                        if val_name in ("math", "os", "sys", "json", "re", "requests", "np", "numpy"):
                            return create_abstention_report(
                                target=target_str,
                                reason=ComplexityAbstentionReason.EXTERNAL_DEPENDENCY,
                                details=(
                                    f"Encountered external library call '{val_name}.{attr_name}()' at line {call_line}. "
                                    "Third-party and external library cost models are not built-in."
                                ),
                                start_line=call_line,
                                end_line=end_line,
                            )

                    return create_abstention_report(
                        target=target_str,
                        reason=ComplexityAbstentionReason.UNKNOWN_CALL,
                        details=(
                            f"Encountered unknown method call '.{attr_name}()' at line {call_line}. "
                            "Dynamic dispatch and unknown method costs require abstention."
                        ),
                        start_line=call_line,
                        end_line=end_line,
                    )

        return None

    def _check_dynamic_loops(
        self,
        node: ast.AST,
        target_str: str,
        start_line: int,
        end_line: int,
    ) -> ComplexityReport | None:
        """Scan AST for data-dependent while loops or dynamic loop bounds."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.While):
                loop_line = getattr(sub, "lineno", start_line)
                # Static constant condition (e.g. while True without break analysis)
                # or data-dependent condition triggers DYNAMIC_BOUNDS
                return create_abstention_report(
                    target=target_str,
                    reason=ComplexityAbstentionReason.DYNAMIC_BOUNDS,
                    details=(
                        f"Encountered while loop at line {loop_line} with data-dependent termination condition. "
                        "Static loop bound cannot be proved with mathematical certainty."
                    ),
                    start_line=loop_line,
                    end_line=end_line,
                )

        return None

    def _classify_container_types(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, ContainerKind]:
        """Classify collection types based on parameter annotations and local assignments."""
        types: dict[str, ContainerKind] = {}

        # 1. Parameter annotations & naming hints
        for arg in node.args.args:
            name = arg.arg
            if name in ("self", "cls"):
                continue
            kind = ContainerKind.OTHER
            if arg.annotation is not None:
                try:
                    ann_str = ast.unparse(arg.annotation).lower()
                except (TypeError, AttributeError, ValueError):
                    ann_str = ""
                if "set" in ann_str:
                    kind = ContainerKind.SET
                elif "dict" in ann_str or "mapping" in ann_str:
                    kind = ContainerKind.DICT
                elif "list" in ann_str or "sequence" in ann_str:
                    kind = ContainerKind.LIST
            else:
                lower_name = name.lower()
                if "set" in lower_name or lower_name == "seen":
                    kind = ContainerKind.SET
                elif "dict" in lower_name or "lookup" in lower_name or "cache" in lower_name:
                    kind = ContainerKind.DICT
                elif "list" in lower_name or "items" in lower_name or "arr" in lower_name:
                    kind = ContainerKind.LIST
            types[name] = kind

        # 2. Local variable assignments
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                target_names: list[str] = []
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            target_names.append(t.id)
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    target_names.append(sub.target.id)

                for target_name in target_names:
                    val = sub.value
                    if val is None:
                        continue
                    if isinstance(val, ast.Set):
                        types[target_name] = ContainerKind.SET
                    elif isinstance(val, ast.Dict):
                        types[target_name] = ContainerKind.DICT
                    elif isinstance(val, (ast.List, ast.ListComp)):
                        types[target_name] = ContainerKind.LIST
                    elif isinstance(val, ast.GeneratorExp):
                        types[target_name] = ContainerKind.GENERATOR
                    elif isinstance(val, ast.Call) and isinstance(val.func, ast.Name):
                        if val.func.id == "set":
                            types[target_name] = ContainerKind.SET
                        elif val.func.id == "dict":
                            types[target_name] = ContainerKind.DICT
                        elif val.func.id == "list":
                            types[target_name] = ContainerKind.LIST
                    else:
                        lower_target = target_name.lower()
                        if "set" in lower_target or lower_target == "seen":
                            types[target_name] = ContainerKind.SET
                        elif "dict" in lower_target or "lookup" in lower_target or "cache" in lower_target:
                            types[target_name] = ContainerKind.DICT
                        elif "list" in lower_target or "arr" in lower_target or "res" in lower_target:
                            types[target_name] = ContainerKind.LIST

        return types

    def _extract_dimension_name(self, expr: ast.expr) -> str | None:
        """Extract dimension name from iterable expression if statically identifiable."""
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            fn = expr.func.id
            if fn == "range" and expr.args:
                last_arg = expr.args[-1]
                if isinstance(last_arg, ast.Name):
                    return last_arg.id
                if (
                    isinstance(last_arg, ast.Call)
                    and isinstance(last_arg.func, ast.Name)
                    and last_arg.func.id == "len"
                    and last_arg.args
                    and isinstance(last_arg.args[0], ast.Name)
                ):
                    return last_arg.args[0].id
            elif fn in ("enumerate", "reversed", "sorted", "list", "set", "iter") and expr.args:
                if isinstance(expr.args[0], ast.Name):
                    return expr.args[0].id
        if isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Name):
            return expr.value.id
        return None

    def _is_constant_range(self, expr: ast.expr) -> bool:
        """Return True if expression is a range with statically constant integer arguments."""
        return bool(
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id == "range"
            and expr.args
            and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in expr.args)
        )

    def _find_outer_loops(self, stmts: list[ast.stmt]) -> list[ast.For]:
        """Find top-level sequential loops across control flow statements."""
        loops: list[ast.For] = []
        for stmt in stmts:
            if isinstance(stmt, ast.For):
                loops.append(stmt)
            elif isinstance(stmt, ast.If):
                loops.extend(self._find_outer_loops(stmt.body))
                loops.extend(self._find_outer_loops(stmt.orelse))
            elif isinstance(stmt, (ast.With, ast.Try)):
                loops.extend(self._find_outer_loops(stmt.body))
        return loops

    def _find_inner_loops(self, body: list[ast.stmt]) -> list[ast.For]:
        """Find immediate child loops inside a loop body."""
        inner: list[ast.For] = []
        for stmt in body:
            if isinstance(stmt, ast.For):
                inner.append(stmt)
            elif isinstance(stmt, ast.If):
                inner.extend(self._find_inner_loops(stmt.body))
                inner.extend(self._find_inner_loops(stmt.orelse))
            elif isinstance(stmt, (ast.With, ast.Try)):
                inner.extend(self._find_inner_loops(stmt.body))
        return inner

    def _evaluate_function_costs(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        target_str: str,
        start_line: int,
        end_line: int,
        is_generator: bool,
    ) -> ComplexityReport:
        """Compute asymptotic bounds, space allocation, flavors, and assumptions."""
        time_cost = ComplexityClassEnum.O_1
        aux_space = ComplexityClassEnum.O_1
        output_space = ComplexityClassEnum.O_1
        is_amortized = False
        is_expected = False
        assumptions: list[str] = []

        param_names = [arg.arg for arg in node.args.args if arg.arg not in ("self", "cls")]
        container_types = self._classify_container_types(node)
        accumulated_containers: set[str] = set()

        outer_loops = self._find_outer_loops(node.body)

        if not outer_loops:
            # Constant-time sequential code without loops
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    if isinstance(sub.func, ast.Name) and sub.func.id in BUILTIN_FUNCTION_COSTS:
                        op = BUILTIN_FUNCTION_COSTS[sub.func.id]
                        time_cost = combine_addition(time_cost, op.time)
                        aux_space = combine_addition(aux_space, op.aux_space)
                    elif isinstance(sub.func, ast.Attribute) and sub.func.attr in STANDARD_METHOD_COSTS:
                        op = STANDARD_METHOD_COSTS[sub.func.attr]
                        time_cost = combine_addition(time_cost, op.time)
                        aux_space = combine_addition(aux_space, op.aux_space)
                        if op.is_amortized:
                            is_amortized = True
                            if op.assumption and op.assumption not in assumptions:
                                assumptions.append(op.assumption)
                        if op.is_expected:
                            is_expected = True
                            if op.assumption and op.assumption not in assumptions:
                                assumptions.append(op.assumption)
                elif isinstance(sub, ast.Compare):
                    for cmp_op, comp in zip(sub.ops, sub.comparators, strict=False):
                        if isinstance(cmp_op, (ast.In, ast.NotIn)):
                            t_name = comp.id if isinstance(comp, ast.Name) else None
                            kind = container_types.get(t_name, ContainerKind.OTHER) if t_name else ContainerKind.OTHER
                            if isinstance(comp, (ast.Set, ast.Dict)) or kind in (ContainerKind.SET, ContainerKind.DICT):
                                is_expected = True
                                hash_assump = "Assumes uniform hash distribution without pathological collisions."
                                if hash_assump not in assumptions:
                                    assumptions.append(hash_assump)
                            elif isinstance(comp, (ast.List, ast.Tuple)) or kind == ContainerKind.LIST:
                                time_cost = combine_addition(time_cost, ComplexityClassEnum.O_N)
                elif isinstance(sub, (ast.ListComp, ast.SetComp, ast.DictComp)) or (
                    isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Slice)
                ):
                    time_cost = combine_addition(time_cost, ComplexityClassEnum.O_N)
        else:
            # One or more top-level loops present
            for loop in outer_loops:
                loop_iter_cost = ComplexityClassEnum.O_N
                d1 = self._extract_dimension_name(loop.iter)

                if self._is_constant_range(loop.iter):
                    loop_iter_cost = ComplexityClassEnum.O_1
                    assumptions.append(f"Constant range loop at line {loop.lineno} runs in O(1) time.")
                else:
                    assumptions.append(f"Assumes loop iteration bound at line {loop.lineno} is n.")

                # Check for nesting depth
                inners = self._find_inner_loops(loop.body)
                deepest_loop: ast.For = loop

                if inners:
                    l2 = inners[0]
                    deepest_loop = l2
                    d2 = self._extract_dimension_name(l2.iter)
                    n2 = ComplexityClassEnum.O_1 if self._is_constant_range(l2.iter) else ComplexityClassEnum.O_N

                    inners_2 = self._find_inner_loops(l2.body)
                    if inners_2:
                        l3 = inners_2[0]
                        deepest_loop = l3
                        n3 = ComplexityClassEnum.O_1 if self._is_constant_range(l3.iter) else ComplexityClassEnum.O_N

                        inners_3 = self._find_inner_loops(l3.body)
                        if inners_3:
                            # 4 or more nested loops exceed closed vocabulary
                            return create_abstention_report(
                                target=target_str,
                                reason=ComplexityAbstentionReason.DYNAMIC_BOUNDS,
                                details=(
                                    f"Four nested loops at line {inners_3[0].lineno} exceed "
                                    "closed vocabulary maximum O(n³)."
                                ),
                                start_line=start_line,
                                end_line=end_line,
                            )

                        # 3 nested loops
                        if loop_iter_cost == ComplexityClassEnum.O_N and n2 == ComplexityClassEnum.O_N and n3 == ComplexityClassEnum.O_N:
                            loop_iter_cost = ComplexityClassEnum.O_N3
                            assumptions.append(f"Triple nested loop at line {loop.lineno} evaluated as O(n³).")
                        else:
                            loop_iter_cost = ComplexityClassEnum.O_N2
                    else:
                        # 2 nested loops
                        if loop_iter_cost == ComplexityClassEnum.O_1 and n2 == ComplexityClassEnum.O_1:
                            loop_iter_cost = ComplexityClassEnum.O_1
                        elif loop_iter_cost == ComplexityClassEnum.O_1:
                            loop_iter_cost = n2
                        elif n2 == ComplexityClassEnum.O_1:
                            pass  # remains O_N
                        else:
                            # Both O(n) - check if distinct dimensions
                            distinct = (
                                d1 is not None
                                and d2 is not None
                                and d1 != d2
                                and (d1 in param_names and d2 in param_names)
                            )
                            if distinct:
                                loop_iter_cost = ComplexityClassEnum.O_NM
                                assumptions.append(
                                    f"Nested loops iterate over distinct dimensions '{d1}' (n) and '{d2}' (m), yielding O(nm)."
                                )
                            else:
                                loop_iter_cost = ComplexityClassEnum.O_N2
                                assumptions.append(
                                    f"Pairwise nested loop at line {l2.lineno} evaluated as O(n²)."
                                )

                # Inspect operations inside deepest loop body
                for sub in ast.walk(deepest_loop):
                    if isinstance(sub, ast.Compare):
                        for cmp_op, comp in zip(sub.ops, sub.comparators, strict=False):
                            if isinstance(cmp_op, (ast.In, ast.NotIn)):
                                t_name = comp.id if isinstance(comp, ast.Name) else None
                                kind = container_types.get(t_name, ContainerKind.OTHER) if t_name else ContainerKind.OTHER
                                if isinstance(comp, (ast.Set, ast.Dict)) or kind in (ContainerKind.SET, ContainerKind.DICT):
                                    is_expected = True
                                    hash_msg = "Assumes uniform hash distribution without pathological collisions."
                                    if hash_msg not in assumptions:
                                        assumptions.append(hash_msg)
                                elif isinstance(comp, (ast.List, ast.Tuple)) or kind == ContainerKind.LIST or t_name in param_names:
                                    scan_msg = f"Membership test 'in {t_name or 'sequence'}' scans sequence in O(n) worst-case time."
                                    if scan_msg not in assumptions:
                                        assumptions.append(scan_msg)
                                    if loop_iter_cost == ComplexityClassEnum.O_N:
                                        if t_name and d1 and t_name != d1 and t_name in param_names:
                                            loop_iter_cost = ComplexityClassEnum.O_NM
                                            assumptions.append(
                                                f"Membership test across distinct collection '{t_name}' yields O(nm) time."
                                            )
                                        else:
                                            loop_iter_cost = ComplexityClassEnum.O_N2
                                            assumptions.append(
                                                "Linear membership scan inside loop yields O(n²) time complexity."
                                            )

                    elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        attr = sub.func.attr
                        if attr in STANDARD_METHOD_COSTS:
                            m_op = STANDARD_METHOD_COSTS[attr]
                            if m_op.is_amortized:
                                is_amortized = True
                                if m_op.assumption and m_op.assumption not in assumptions:
                                    assumptions.append(m_op.assumption)
                            if m_op.is_expected:
                                is_expected = True
                                if m_op.assumption and m_op.assumption not in assumptions:
                                    assumptions.append(m_op.assumption)
                            if m_op.time == ComplexityClassEnum.O_N and loop_iter_cost == ComplexityClassEnum.O_N:
                                loop_iter_cost = ComplexityClassEnum.O_N2
                                assumptions.append(
                                    f"Linear sequence method '{attr}' inside loop yields O(n²) time complexity."
                                )
                            if isinstance(sub.func.value, ast.Name):
                                accumulated_containers.add(sub.func.value.id)

                    elif (
                        isinstance(sub, ast.Subscript)
                        and isinstance(sub.slice, ast.Slice)
                        and loop_iter_cost == ComplexityClassEnum.O_N
                    ):
                        loop_iter_cost = ComplexityClassEnum.O_N2
                        assumptions.append("Slicing inside loop creates O(n) copies yielding O(n²) time.")

                time_cost = combine_addition(time_cost, loop_iter_cost)

            if len(outer_loops) > 1:
                assumptions.append("Sequential loop addition: O(n) + O(n) = O(n).")

        # -------------------------------------------------------------------------
        # Return & Space Evaluation (Auxiliary vs Output Space)
        # -------------------------------------------------------------------------
        returned_comps: list[ast.AST] = []
        returned_slices: list[ast.AST] = []
        returned_names: set[str] = set()
        returned_calls: list[ast.Call] = []
        has_returned_gen_exp = False
        returns_scalar = False

        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and sub.value is not None:
                val = sub.value
                if isinstance(val, (ast.ListComp, ast.DictComp, ast.SetComp)):
                    returned_comps.append(val)
                elif isinstance(val, ast.GeneratorExp):
                    has_returned_gen_exp = True
                elif isinstance(val, ast.Subscript) and isinstance(val.slice, ast.Slice):
                    returned_slices.append(val)
                elif isinstance(val, ast.Name):
                    returned_names.add(val.id)
                elif isinstance(val, ast.Call) and (
                    (isinstance(val.func, ast.Name) and val.func.id in ("sorted", "list", "set", "dict"))
                    or (isinstance(val.func, ast.Attribute) and val.func.attr == "copy")
                ):
                    returned_calls.append(val)
                elif isinstance(val, (ast.Constant, ast.UnaryOp, ast.BinOp)) or (
                    isinstance(val, ast.Call)
                    and isinstance(val.func, ast.Name)
                    and val.func.id in ("len", "int", "float", "bool", "str")
                ):
                    returns_scalar = True

        # Output space
        if is_generator:
            output_space = ComplexityClassEnum.O_1
            assumptions.append("Generator expression / yield returns lazy iterator in O(1) output space.")
        elif has_returned_gen_exp:
            output_space = ComplexityClassEnum.O_1
            assumptions.append("Generator expression returns lazy iterator in O(1) output space.")
        elif returned_comps:
            output_space = ComplexityClassEnum.O_N
            assumptions.append("Returned list/set/dict comprehension materializes n elements.")
        elif returned_slices:
            output_space = ComplexityClassEnum.O_N
            assumptions.append("Returned slice creates a copy materializing O(n) elements in output space.")
        elif returned_calls:
            output_space = ComplexityClassEnum.O_N
            assumptions.append("Returned collection call materializes n elements in output space.")
        elif any(name in accumulated_containers for name in returned_names):
            output_space = ComplexityClassEnum.O_N
            acc_name = next(name for name in returned_names if name in accumulated_containers)
            assumptions.append(f"Returned container '{acc_name}' accumulates n elements in output space.")
        elif returns_scalar:
            output_space = ComplexityClassEnum.O_1
        else:
            output_space = ComplexityClassEnum.O_1

        # Auxiliary space
        all_comps = [sub for sub in ast.walk(node) if isinstance(sub, (ast.ListComp, ast.DictComp, ast.SetComp))]
        temp_comps = [c for c in all_comps if c not in returned_comps]
        if temp_comps:
            aux_space = combine_addition(aux_space, ComplexityClassEnum.O_N)
            assumptions.append("Temporary list/set/dict comprehension allocates O(n) auxiliary heap space.")

        all_gens = [sub for sub in ast.walk(node) if isinstance(sub, ast.GeneratorExp)]
        if all_gens:
            assumptions.append("Generator expression consumes O(1) auxiliary space (lazy evaluation).")

        all_slices = [sub for sub in ast.walk(node) if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Slice)]
        temp_slices = [s for s in all_slices if s not in returned_slices]
        if temp_slices:
            aux_space = combine_addition(aux_space, ComplexityClassEnum.O_N)
            time_cost = combine_addition(time_cost, ComplexityClassEnum.O_N)
            assumptions.append("Intermediate slicing creates an O(n) auxiliary copy.")

        has_sort = any(
            (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "sorted")
            or (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "sort")
            for sub in ast.walk(node)
        )
        if has_sort:
            aux_space = combine_addition(aux_space, ComplexityClassEnum.O_N)
            assumptions.append("Timsort / Powersort requires O(n) auxiliary run buffers.")

        internal_accumulators = [c for c in accumulated_containers if c not in returned_names]
        if internal_accumulators:
            aux_space = combine_addition(aux_space, ComplexityClassEnum.O_N)
            assumptions.append(f"Internal container '{internal_accumulators[0]}' consumes O(n) auxiliary space.")

        # Deduplicate assumptions
        unique_assumptions: list[str] = []
        for a in assumptions:
            if a not in unique_assumptions:
                unique_assumptions.append(a)

        confidence = ConfidenceEnum.HIGH if not unique_assumptions else ConfidenceEnum.MEDIUM

        details_msg = (
            f"Derived {time_cost.value} time complexity, {aux_space.value} auxiliary space, "
            f"and {output_space.value} output space under CPython 3.11/3.12 semantics."
        )

        return ComplexityReport(
            target=target_str,
            time_complexity=time_cost,
            auxiliary_space=aux_space,
            output_space=output_space,
            confidence=confidence,
            is_amortized=is_amortized,
            is_expected=is_expected,
            assumptions=unique_assumptions,
            abstention_reason=None,
            details=details_msg,
            start_line=start_line,
            end_line=end_line,
        )

    def _analyze_module_body(self, stmts: list[ast.stmt]) -> ComplexityReport:
        """Fallback evaluation for top-level module statements."""
        return ComplexityReport(
            target=self.target_name,
            time_complexity=ComplexityClassEnum.O_1,
            auxiliary_space=ComplexityClassEnum.O_1,
            output_space=ComplexityClassEnum.O_1,
            confidence=ConfidenceEnum.LOW,
            is_amortized=False,
            is_expected=False,
            assumptions=["Top-level module execution evaluated as constant script setup."],
            abstention_reason=None,
            details="No function definition specified; evaluated top-level script statements.",
            start_line=1,
            end_line=len(self.source_lines) or 1,
        )


def analyze_complexity(
    target: TargetRecord | str,
    source_text: str | None = None,
    selector: str | None = None,
) -> ComplexityReport:
    """Analyze static algorithmic complexity under the formal CPython cost model.

    Args:
        target: Target file record or file path string.
        source_text: Source code text (read from disk if None).
        selector: Optional function selector (e.g. 'compute' or 'Worker.process').

    Returns:
        Validated, schema-conforming ComplexityReport.
    """
    target_path = Path(target.absolute_path if isinstance(target, TargetRecord) else target)
    target_name = target.path if isinstance(target, TargetRecord) else str(target_path)

    text = source_text
    if text is None:
        try:
            text = target_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return create_abstention_report(
                target=target_name,
                reason=ComplexityAbstentionReason.UNSUPPORTED_SYNTAX,
                details=f"Failed to read target source file: {exc}",
            )

    analyzer = CostModelAnalyzer(target_name=target_name, source_text=text)
    return analyzer.analyze_selector(selector=selector)


def analyze_file_complexity(
    target: TargetRecord | str,
    source_text: str | None = None,
) -> list[ComplexityReport]:
    """Analyze static algorithmic complexity for all functions/methods in target.

    Args:
        target: Target file record or file path string.
        source_text: Source code text (read from disk if None).

    Returns:
        List of validated, schema-conforming ComplexityReport instances.
    """
    target_path = Path(target.absolute_path if isinstance(target, TargetRecord) else target)
    target_name = target.path if isinstance(target, TargetRecord) else str(target_path)

    text = source_text
    if text is None:
        try:
            text = target_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [
                create_abstention_report(
                    target=target_name,
                    reason=ComplexityAbstentionReason.UNSUPPORTED_SYNTAX,
                    details=f"Failed to read target source file: {exc}",
                )
            ]

    analyzer = CostModelAnalyzer(target_name=target_name, source_text=text)
    return analyzer.analyze_all()
