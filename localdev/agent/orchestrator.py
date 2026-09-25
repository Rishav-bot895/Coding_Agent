"""Central orchestrator coordinating language adapters, sessions, and agent workflows.

Orchestrator strictly decouples workflow coordination from language-specific
semantics by routing all language operations through the LanguageAdapter contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.agent.session import Session
from localdev.errors import InferenceError, UnsupportedLanguageError
from localdev.languages.base import (
    AdapterRegistry,
    LanguageAdapter,
    get_default_registry,
)
from localdev.schemas import (
    AnalysisReport,
    ASTFacts,
    ComplexityReport,
    DetectionConfidence,
    DetectionResult,
    DiagnosisAbstention,
    DiagnosisAbstentionReason,
    DiagnosisRecord,
    DiagnosticRecord,
    ExecutionResult,
    ExecutionSpec,
    InferenceMetadata,
    TargetInfoRecord,
    TargetRecord,
    ValidationReport,
)

if TYPE_CHECKING:
    from localdev.inference.base import BaseInferenceClient


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

    def diagnose(
        self,
        target: TargetRecord,
        analysis_report: AnalysisReport,
        execution_result: ExecutionResult | None = None,
        source_text: str | None = None,
        inference_client: BaseInferenceClient | None = None,
        model: str | None = None,
    ) -> tuple[DiagnosisRecord | DiagnosisAbstention, InferenceMetadata | None]:
        """Generate evidence-grounded bug diagnosis using local SLM.

        1. Cross-file verification: if execution failed but traceback has no target frames,
           abstain safely with explicit technical limitation.
        2. Bounded prompt assembly: build compact context <= 1,200 tokens.
        3. Inference client execution: connect to local SLM or abstain if unavailable.
        4. Strict validation & single retry: validate schema, grounding, and line bounds.
        """
        # 1. Cross-file check
        if execution_result is not None and (execution_result.exit_code != 0 or execution_result.timed_out):
            target_frames = [f for f in execution_result.frames if f.is_target]
            external_frames = [f for f in execution_result.frames if not f.is_target]
            if not target_frames and external_frames:
                fault_site = f"{external_frames[-1].file_path}:{external_frames[-1].line_number}"
                return (
                    DiagnosisAbstention(
                        target=target.path,
                        reason=DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE,
                        details=(
                            f"Traceback indicates failure occurred in external library or dependency ({fault_site}) "
                            f"without frames inside the target file. localdev enforces a single-target boundary "
                            f"and only inspects '{target.path}'."
                        ),
                        retry_attempted=False,
                    ),
                    None,
                )

        # 2. Source text resolution
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

        # 3. Context assembly
        from localdev.agent.context_builder import build_diagnosis_context
        from localdev.errors import PromptBudgetExceededError

        try:
            context = build_diagnosis_context(
                target=target,
                source_text=source,
                analysis_report=analysis_report,
                execution_result=execution_result,
            )
        except PromptBudgetExceededError as exc:
            return (
                DiagnosisAbstention(
                    target=target.path,
                    reason=DiagnosisAbstentionReason.PROMPT_BUDGET_EXCEEDED,
                    details=f"Prompt context exceeded 1,200 token budget: {exc}",
                    retry_attempted=False,
                ),
                None,
            )

        # 4. Inference client resolution
        client = inference_client
        if client is None:
            from localdev.inference.ollama_client import OllamaClient

            try:
                client = OllamaClient()
            except (InferenceError, OSError, RuntimeError) as exc:
                return (
                    DiagnosisAbstention(
                        target=target.path,
                        reason=DiagnosisAbstentionReason.MODEL_UNAVAILABLE,
                        details=f"Failed to initialize local inference client: {exc}",
                        retry_attempted=False,
                    ),
                    None,
                )

        if not client.is_available():
            return (
                DiagnosisAbstention(
                    target=target.path,
                    reason=DiagnosisAbstentionReason.MODEL_UNAVAILABLE,
                    details="Local Ollama service (127.0.0.1:11434) is not running or unreachable.",
                    retry_attempted=False,
                ),
                None,
            )

        # 5. Execute with validation and single automated retry
        from localdev.inference.response_validator import execute_diagnosis_with_retry

        total_lines = len(source.splitlines()) if source else 0
        return execute_diagnosis_with_retry(
            client=client,
            messages=context.to_messages(),
            target_path=target.path,
            total_lines=total_lines,
            evidence_manifest=context.evidence_manifest,
            model=model,
        )

    def analyse(
        self,
        target: TargetRecord,
        source_text: str | None = None,
        diagnose: bool = False,
        inference_client: BaseInferenceClient | None = None,
    ) -> AnalysisReport:
        """Run the deterministic analyse workflow for a target file."""
        from localdev.agent.evidence import collect_analysis_evidence

        report = collect_analysis_evidence(self, target, source_text=source_text)
        if diagnose:
            diag, meta = self.diagnose(
                target=target,
                analysis_report=report,
                execution_result=None,
                source_text=source_text,
                inference_client=inference_client,
            )
            report.diagnosis = diag
            report.inference_metadata = meta
        return report

    def debug(
        self,
        target: TargetRecord,
        target_args: Sequence[str] | None = None,
        stdin_file: str | Path | None = None,
        timeout: float | None = None,
        fail_on_job_failure: bool = False,
        diagnose: bool = False,
        inference_client: BaseInferenceClient | None = None,
    ) -> ExecutionResult:
        """Run the deterministic debug execution and traceback evidence workflow."""
        from localdev.agent.evidence import collect_debug_evidence

        exec_result = collect_debug_evidence(
            self,
            target,
            target_args=target_args,
            stdin_file=stdin_file,
            timeout=timeout,
            fail_on_job_failure=fail_on_job_failure,
        )
        if diagnose:
            analysis_report = self.analyse(target)
            diag, meta = self.diagnose(
                target=target,
                analysis_report=analysis_report,
                execution_result=exec_result,
                inference_client=inference_client,
            )
            exec_result.diagnosis = diag
            exec_result.inference_metadata = meta
        return exec_result


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

