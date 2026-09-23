"""Abstract base class and registry defining the language adapter interface for localdev.

Decouples all language-specific syntax checking, AST fact extraction, linter diagnostics,
execution preparation, algorithmic complexity analysis, and candidate patch validation
behind a strongly typed, schema-validated contract.
"""

from __future__ import annotations

import abc
from pathlib import Path

from localdev.schemas import (
    ASTFacts,
    ComplexityReport,
    DetectionConfidence,
    DetectionResult,
    DiagnosticRecord,
    ExecutionResult,
    ExecutionSpec,
    LanguageCapabilities,
    TargetRecord,
    ValidationReport,
)


class LanguageAdapter(abc.ABC):
    """Abstract base class defining the language adapter contract."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Normalized language identifier (e.g. 'python')."""
        ...

    @property
    @abc.abstractmethod
    def file_extensions(self) -> tuple[str, ...]:
        """Recognized file extensions (e.g. ('.py', '.pyw'))."""
        ...

    @property
    @abc.abstractmethod
    def capabilities(self) -> LanguageCapabilities:
        """Capabilities supported by this language adapter."""
        ...

    @abc.abstractmethod
    def detect_confidence(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> DetectionResult:
        """Evaluate detection confidence for target without code execution.

        Returns DetectionResult with CERTAIN, PROBABLE, or UNSUPPORTED.
        """
        ...

    @abc.abstractmethod
    def check_syntax(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        """Check syntax of target or source text without execution.

        Returns a list of DiagnosticRecord if syntax errors are found,
        or an empty list if syntax is valid.
        """
        ...

    @abc.abstractmethod
    def extract_ast_facts(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> ASTFacts:
        """Extract bounded structural facts (functions, classes) from AST."""
        ...

    @abc.abstractmethod
    def run_diagnostics(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        """Run isolated static analysis diagnostics (e.g. Ruff linter)."""
        ...

    @abc.abstractmethod
    def prepare_execution(
        self,
        target: TargetRecord,
        args: list[str] | None = None,
    ) -> ExecutionSpec:
        """Prepare command-line arguments and environment for controlled execution."""
        ...

    @abc.abstractmethod
    def analyze_complexity(
        self,
        target: TargetRecord,
        source_text: str | None = None,
        selector: str | None = None,
    ) -> ComplexityReport:
        """Perform static algorithmic complexity analysis without executing code."""
        ...

    @abc.abstractmethod
    def validate_candidate(
        self,
        target: TargetRecord,
        candidate_path: Path | str,
        baseline_result: ExecutionResult | None = None,
        expected_stdout: str | None = None,
        expected_stdout_contains: str | None = None,
        expected_exit: int | None = None,
    ) -> ValidationReport:
        """Empirically evaluate a candidate patch across validation tiers (Levels A-D)."""
        ...


class AdapterRegistry:
    """Registry managing available language adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, LanguageAdapter] = {}

    def register(self, adapter: LanguageAdapter) -> None:
        """Register a language adapter instance."""
        if not isinstance(adapter, LanguageAdapter):
            raise TypeError(f"Expected LanguageAdapter instance, got {type(adapter).__name__}")
        self._adapters[adapter.name.lower()] = adapter

    def get(self, name: str) -> LanguageAdapter | None:
        """Retrieve an adapter by its normalized language name."""
        return self._adapters.get(name.lower())

    def list_adapters(self) -> list[str]:
        """Return a sorted list of registered language names."""
        return sorted(self._adapters.keys())

    def clear(self) -> None:
        """Clear all registered adapters (useful for unit testing)."""
        self._adapters.clear()

    def detect_adapter(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> tuple[LanguageAdapter | None, DetectionResult]:
        """Detect the best-matching language adapter for the given target.

        Evaluates registered adapters in order, returning the adapter with
        CERTAIN or PROBABLE confidence, or (None, unsupported_result) if none match.
        """
        best_adapter: LanguageAdapter | None = None
        best_result: DetectionResult | None = None

        for adapter in self._adapters.values():
            result = adapter.detect_confidence(target, source_text=source_text)
            if result.confidence == DetectionConfidence.CERTAIN:
                return adapter, result
            if (
                result.confidence == DetectionConfidence.PROBABLE
                and (best_result is None or best_result.confidence == DetectionConfidence.UNSUPPORTED)
            ):
                best_adapter = adapter
                best_result = result

        if best_adapter is not None and best_result is not None:
            return best_adapter, best_result

        unsupported_result = DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=["No registered language adapter matched the target file."],
            matched_extension=None,
            has_shebang=False,
        )
        return None, unsupported_result


_DEFAULT_REGISTRY: AdapterRegistry | None = None


def get_default_registry() -> AdapterRegistry:
    """Return the global default adapter registry, initializing with built-ins if needed."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = AdapterRegistry()
        from localdev.languages.python.adapter import PythonAdapter

        _DEFAULT_REGISTRY.register(PythonAdapter())
    return _DEFAULT_REGISTRY


def register_adapter(adapter: LanguageAdapter) -> None:
    """Register an adapter with the default registry."""
    get_default_registry().register(adapter)


def get_adapter(name: str) -> LanguageAdapter | None:
    """Retrieve an adapter by name from the default registry."""
    return get_default_registry().get(name)


def detect_adapter(
    target: TargetRecord,
    source_text: str | None = None,
) -> tuple[LanguageAdapter | None, DetectionResult]:
    """Detect the best-matching language adapter using the default registry."""
    return get_default_registry().detect_adapter(target, source_text=source_text)
