"""Central orchestrator coordinating language adapters, sessions, and agent workflows.

Orchestrator strictly decouples workflow coordination from language-specific
semantics by routing all language operations through the LanguageAdapter contract.
"""

from __future__ import annotations

from pathlib import Path

from localdev.agent.session import Session
from localdev.errors import UnsupportedLanguageError
from localdev.languages.base import (
    AdapterRegistry,
    LanguageAdapter,
    get_default_registry,
)
from localdev.schemas import (
    ASTFacts,
    ComplexityReport,
    DetectionConfidence,
    DetectionResult,
    DiagnosticRecord,
    ExecutionResult,
    ExecutionSpec,
    TargetInfoRecord,
    TargetRecord,
    ValidationReport,
)


class Orchestrator:
    """Central agent orchestrator coordinating adapters, sessions, evidence, and workflows."""

    def __init__(
        self,
        adapter: LanguageAdapter | None = None,
        registry: AdapterRegistry | None = None,
        session: Session | None = None,
    ) -> None:
        self._injected_adapter = adapter
        self._registry = registry or get_default_registry()
        self._session = session

    @property
    def adapter(self) -> LanguageAdapter | None:
        """The explicitly injected language adapter, if any."""
        return self._injected_adapter

    @property
    def session(self) -> Session | None:
        """The active isolated session, if any."""
        return self._session

    def resolve_adapter(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> LanguageAdapter:
        """Resolve the appropriate language adapter for the target.

        Uses injected adapter if present; otherwise queries the adapter registry.
        Raises UnsupportedLanguageError if no matching adapter is found with
        CERTAIN or PROBABLE confidence.
        """
        if self._injected_adapter is not None:
            return self._injected_adapter

        adapter, result = self._registry.detect_adapter(target, source_text=source_text)
        if adapter is not None and result.confidence in (
            DetectionConfidence.CERTAIN,
            DetectionConfidence.PROBABLE,
        ):
            return adapter

        raise UnsupportedLanguageError(
            f"Target '{target.path}' is not supported by any registered language adapter.",
            detected_language=result.language,
        )

    def get_info(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> TargetInfoRecord:
        """Collect target file attributes, line counts, and language detection result."""
        source = source_text
        if source is None:
            target_path = Path(target.absolute_path)
            try:
                source = target_path.read_text(encoding=target.encoding)
            except (OSError, UnicodeDecodeError):
                try:
                    source = target_path.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeDecodeError):
                    source = ""

        total_lines = len(source.splitlines()) if source else 0
        detection = self.detect(target, source_text=source)
        return TargetInfoRecord(
            target=target,
            total_lines=total_lines,
            detection=detection,
        )

    def detect(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> DetectionResult:
        """Detect the language and confidence for the target file."""
        if self._injected_adapter is not None:
            return self._injected_adapter.detect_confidence(target, source_text=source_text)
        _, result = self._registry.detect_adapter(target, source_text=source_text)
        return result

    def check_syntax(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        """Check syntax of the target file using the resolved language adapter."""
        adapter = self.resolve_adapter(target, source_text=source_text)
        return adapter.check_syntax(target, source_text=source_text)

    def extract_ast_facts(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> ASTFacts:
        """Extract bounded structural AST facts using the resolved language adapter."""
        adapter = self.resolve_adapter(target, source_text=source_text)
        return adapter.extract_ast_facts(target, source_text=source_text)

    def run_diagnostics(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        """Run isolated static analysis diagnostics using the resolved language adapter."""
        adapter = self.resolve_adapter(target, source_text=source_text)
        return adapter.run_diagnostics(target, source_text=source_text)

    def prepare_execution(
        self,
        target: TargetRecord,
        args: list[str] | None = None,
    ) -> ExecutionSpec:
        """Prepare command-line arguments and environment for controlled execution."""
        adapter = self.resolve_adapter(target)
        return adapter.prepare_execution(target, args=args)

    def analyze_complexity(
        self,
        target: TargetRecord,
        source_text: str | None = None,
        selector: str | None = None,
    ) -> ComplexityReport:
        """Perform static algorithmic complexity analysis using the resolved language adapter."""
        adapter = self.resolve_adapter(target, source_text=source_text)
        return adapter.analyze_complexity(target, source_text=source_text, selector=selector)

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
        adapter = self.resolve_adapter(target)
        return adapter.validate_candidate(
            target,
            candidate_path=candidate_path,
            baseline_result=baseline_result,
            expected_stdout=expected_stdout,
            expected_stdout_contains=expected_stdout_contains,
            expected_exit=expected_exit,
        )

