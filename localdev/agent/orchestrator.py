"""Central orchestrator coordinating language adapters, sessions, and agent workflows.

Orchestrator strictly decouples workflow coordination from language-specific
semantics by routing all language operations through the LanguageAdapter contract.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.agent.session import Session
from localdev.errors import InferenceError, UnsupportedLanguageError
from localdev.languages.base import (
    AdapterRegistry,
    LanguageAdapter,
    get_default_registry,
)
from localdev.patching import PatchCandidate
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
    EditProposalRecord,
    ExecutionResult,
    ExecutionSpec,
    FixReport,
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

    def propose_fix(
        self,
        target: TargetRecord,
        source_text: str | None = None,
        analysis_report: AnalysisReport | None = None,
        execution_result: ExecutionResult | None = None,
        diagnosis: DiagnosisRecord | None = None,
        inference_client: BaseInferenceClient | None = None,
        model: str | None = None,
        prompt_budget: int | None = None,
    ) -> tuple[
        EditProposalRecord | None,
        PatchCandidate | None,
        DiagnosisAbstention | None,
        InferenceMetadata | None,
    ]:
        """Generate structured edit proposal and in-memory candidate using local SLM with retry.

        Enforces:
        - Assembly of compact context within prompt token budget.
        - Grammar-constrained decoding against EditProposalRecord.
        - Strict source line verification via EditProposalValidator.
        - In-memory candidate application via PatchApplier.
        - Verification that candidate source parses cleanly (Level A).
        - Exactly 1 automated retry on schema/line/AST failure.
        - Safe abstention if retry fails or client is unavailable.
        """
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

        # Context assembly
        from localdev.agent.context_builder import build_edit_proposal_context
        from localdev.errors import PromptBudgetExceededError

        try:
            context = build_edit_proposal_context(
                target=target,
                source_text=source,
                diagnosis=diagnosis,
                analysis_report=analysis_report,
                execution_result=execution_result,
                prompt_budget=prompt_budget,
            )
        except PromptBudgetExceededError as exc:
            return (
                None,
                None,
                DiagnosisAbstention(
                    target=target.path,
                    reason=DiagnosisAbstentionReason.PROMPT_BUDGET_EXCEEDED,
                    details=f"Prompt context exceeded token budget: {exc}",
                    retry_attempted=False,
                ),
                None,
            )

        # Client resolution
        client = inference_client
        if client is None:
            from localdev.inference.ollama_client import OllamaClient

            try:
                client = OllamaClient()
            except (InferenceError, OSError, RuntimeError) as exc:
                return (
                    None,
                    None,
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
                None,
                None,
                DiagnosisAbstention(
                    target=target.path,
                    reason=DiagnosisAbstentionReason.MODEL_UNAVAILABLE,
                    details="Local Ollama service (127.0.0.1:11434) is not running or unreachable.",
                    retry_attempted=False,
                ),
                None,
            )

        # Execute proposal generation with single automated retry
        from localdev.inference.response_validator import (
            execute_edit_proposal_with_retry,
        )

        return execute_edit_proposal_with_retry(
            client=client,
            messages=context.to_messages(),
            target=target,
            source_text=source,
            model=model,
        )

    def fix(
        self,
        target: TargetRecord,
        source_text: str | None = None,
        apply: bool = False,
        propose_only: bool = False,
        expected_stdout: str | None = None,
        expected_stdout_contains: str | None = None,
        expected_exit: int | None = None,
        target_args: Sequence[str] | None = None,
        stdin_file: str | Path | None = None,
        timeout: float | None = None,
        fail_on_job_failure: bool = False,
        interactive: bool = True,
        prompt_func: Callable[[str], bool] | None = None,
        inference_client: BaseInferenceClient | None = None,
        model: str | None = None,
        create_backup: bool = True,
    ) -> FixReport:
        """Run the end-to-end fix workflow for a single target file.

        Workflow:
        1. Collect deterministic static and runtime evidence (analyse + debug).
        2. If no defect is found and no oracle is violated, report healthy status.
        3. Request evidence-grounded diagnosis from local SLM.
           If diagnosis abstains, emit FixReport with abstention.
        4. Request structured edit proposal with 1 automated retry policy.
           If proposal abstains, emit FixReport with abstention.
        5. Evaluate candidate across validation tiers (Levels A-D).
        6. If propose_only is True, return report without prompting or modifying file.
        7. If write authority is confirmed (via --apply or interactive prompt):
           Execute atomic replacement with native Win32 ReplaceFileW and backup.
           If declined, return clean unapplied report.
        """
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

        # Step 1: Collect deterministic evidence
        analysis_report = self.analyse(target, source_text=source)
        execution_result: ExecutionResult | None = None
        if analysis_report.syntax_valid:
            execution_result = self.debug(
                target=target,
                target_args=target_args,
                stdin_file=stdin_file,
                timeout=timeout,
                fail_on_job_failure=fail_on_job_failure,
            )

        # Check if defect exists
        has_syntax_error = not analysis_report.syntax_valid
        has_static_findings = len(analysis_report.diagnostics) > 0
        has_runtime_error = (
            execution_result is not None
            and (
                execution_result.exit_code != 0
                or execution_result.timed_out
                or execution_result.error_signature is not None
            )
        )
        has_oracle_request = (
            expected_stdout is not None
            or expected_stdout_contains is not None
            or expected_exit is not None
        )

        oracle_mismatch = False
        if has_oracle_request and execution_result is not None:
            if expected_exit is not None and execution_result.exit_code != expected_exit:
                oracle_mismatch = True
            if expected_stdout is not None and execution_result.stdout.strip() != expected_stdout.strip():
                oracle_mismatch = True
            if expected_stdout_contains is not None and expected_stdout_contains not in execution_result.stdout:
                oracle_mismatch = True

        if not (has_syntax_error or has_static_findings or has_runtime_error or oracle_mismatch):
            return FixReport(
                target=target,
                message="No defect detected; target is already syntactically valid, has no static diagnostics, and executes cleanly.",
            )

        # Step 2: Request diagnosis
        diag_record_or_abstention, diag_meta = self.diagnose(
            target=target,
            analysis_report=analysis_report,
            execution_result=execution_result,
            source_text=source,
            inference_client=inference_client,
            model=model,
        )

        if isinstance(diag_record_or_abstention, DiagnosisAbstention):
            return FixReport(
                target=target,
                diagnosis=diag_record_or_abstention,
                abstention=diag_record_or_abstention,
                message=f"Fix workflow abstained during diagnosis: {diag_record_or_abstention.details}",
                inference_metadata=diag_meta,
            )

        diagnosis: DiagnosisRecord = diag_record_or_abstention

        # Step 3: Propose fix
        proposal, candidate, prop_abstention, prop_meta = self.propose_fix(
            target=target,
            source_text=source,
            analysis_report=analysis_report,
            execution_result=execution_result,
            diagnosis=diagnosis,
            inference_client=inference_client,
            model=model,
        )

        if prop_abstention is not None:
            return FixReport(
                target=target,
                diagnosis=diagnosis,
                proposal=proposal,
                abstention=prop_abstention,
                message=f"Fix workflow abstained during patch proposal: {prop_abstention.details}",
                inference_metadata=prop_meta,
            )

        if candidate is None or proposal is None:
            return FixReport(
                target=target,
                diagnosis=diagnosis,
                message="Failed to generate patch candidate.",
                inference_metadata=prop_meta,
            )

        # Step 4: Validate candidate
        from localdev.patching.atomic_write import (
            atomic_replace_file,
            get_same_volume_staging_dir,
        )

        staging_dir: Path
        if self.session is not None and self.session.same_volume_staging_dir is not None:
            staging_dir = self.session.same_volume_staging_dir
        else:
            staging_dir = get_same_volume_staging_dir(target.absolute_path)

        staged_candidate_path = candidate.write_to_staging(staging_dir, "candidate.py")
        validation_report = self.validate_candidate(
            target=target,
            candidate_path=staged_candidate_path,
            baseline_result=execution_result,
            expected_stdout=expected_stdout,
            expected_stdout_contains=expected_stdout_contains,
            expected_exit=expected_exit,
        )

        # Step 5: Propose-only review
        if propose_only:
            return FixReport(
                target=target,
                diagnosis=diagnosis,
                proposal=proposal,
                diff=candidate.diff,
                validation=validation_report,
                applied=False,
                message="Fix proposed successfully. Use --apply to write patch to disk.",
                inference_metadata=prop_meta,
            )

        # Step 6: Confirmation & Atomic replacement
        has_write_auth = apply
        interactive_mode = interactive and not apply

        repl_result = atomic_replace_file(
            target_path=target.absolute_path,
            candidate=candidate,
            baseline_sha256=target.sha256,
            has_write_authority=has_write_auth,
            interactive=interactive_mode,
            create_backup=create_backup,
            staging_dir=staging_dir,
            prompt_func=prompt_func,
        )

        if repl_result.success:
            backup_str = str(repl_result.backup_path) if repl_result.backup_path else None
            return FixReport(
                target=target,
                diagnosis=diagnosis,
                proposal=proposal,
                diff=candidate.diff,
                validation=validation_report,
                applied=True,
                backup_path=backup_str,
                message=f"Patch successfully applied to '{target.path}'.",
                inference_metadata=prop_meta,
            )
        else:
            return FixReport(
                target=target,
                diagnosis=diagnosis,
                proposal=proposal,
                diff=candidate.diff,
                validation=validation_report,
                applied=False,
                declined=True,
                message=repl_result.message or "Patch application declined by user.",
                inference_metadata=prop_meta,
            )


