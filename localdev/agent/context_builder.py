"""Compact, boundary-safe model context assembly within prompt budget.

Enforces:
- Formal separation of:
  1. Model context window: configured total capacity in Ollama (num_ctx: 2048).
  2. Application prompt budget: hard upper limit (1,200 tokens) enforced by context builder.
  3. Maximum generation budget: hard upper limit on model output (num_predict: 600).
  4. Application safety margin: remaining 248 tokens reserved for protocol overhead,
     JSON schema representation, tokenizer variance, and chat framing.
- Invariant: prompt_budget (1,200) + output_budget (600) + safety_margin (248) <= context_window (2,048).
- Strict enforcement of prompt_budget <= 1200 tokens using tokenizer counting or conservative fallback.
- Priority order for evidence inclusion:
  1. System prompt and strict JSON Schema output instructions.
  2. Primary failure signature (exception class, message, top target frame line).
  3. Target function/method AST source excerpt containing the fault.
  4. Specific Ruff diagnostics targeting lines within that excerpt.
  5. Surrounding context lines within the target function (up to available prompt budget).
  6. Broader AST outline / secondary diagnostics if budget permits.
- Truncation metadata tracking omitted categories and was_truncated flag.
- Strict single-target boundary (only target file excerpts, external frames summarized without code).
- Defensive framing enclosing untrusted user code in delimiters with escape protection.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from localdev.config import LocaldevConfig, estimate_tokens
from localdev.constants import (
    PROMPT_BUDGET_TOKENS,
)
from localdev.errors import PromptBudgetExceededError
from localdev.inference.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    wrap_untrusted_code,
)
from localdev.schemas import (
    AnalysisReport,
    ASTFacts,
    ASTFunctionFact,
    DiagnosticRecord,
    ExecutionResult,
    InferenceMetadata,
    TargetRecord,
)

# Hard upper ceiling for prompt budget as specified in contract
MAX_ALLOWED_PROMPT_BUDGET: Final[int] = PROMPT_BUDGET_TOKENS  # 1200


class BuiltPromptContext(BaseModel):
    """Result of assembling compact, bounded model context."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(description="System role and operational instructions.")
    user_prompt: str = Field(description="Evidence-grounded user task prompt.")
    metadata: InferenceMetadata = Field(description="Inference budget and truncation metadata.")
    evidence_manifest: list[str] = Field(
        default_factory=list,
        description="Available evidence IDs explicitly provided in the prompt for grounding.",
    )
    fault_line: int | None = Field(
        default=None, description="Identified line number of primary fault or failure."
    )
    fault_function: str | None = Field(
        default=None, description="Name of enclosing function or method if identified."
    )
    included_categories: list[str] = Field(
        default_factory=list, description="Evidence categories successfully included in prompt."
    )
    omitted_categories: list[str] = Field(
        default_factory=list, description="Evidence categories omitted or truncated due to budget."
    )
    was_truncated: bool = Field(
        default=False, description="True if any evidence or excerpt was omitted or truncated."
    )

    @property
    def full_prompt(self) -> str:
        """Combined prompt string matching the token evaluation sequence."""
        return f"{self.system_prompt}\n\n{self.user_prompt}"

    @property
    def estimated_tokens(self) -> int:
        """Estimated prompt token count."""
        return self.metadata.estimated_prompt_tokens

    def to_messages(self) -> list[dict[str, str]]:
        """Format as chat messages for chat_structured inference."""
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_prompt},
        ]


def _find_fault_location(
    analysis_report: AnalysisReport | None,
    execution_result: ExecutionResult | None,
) -> tuple[int | None, str | None]:
    """Identify the 1-based target line and failure category of the fault."""
    # 1. Check runtime execution error signature
    if execution_result is not None:
        if execution_result.error_signature and execution_result.error_signature.top_target_line:
            return execution_result.error_signature.top_target_line, "runtime_signature"
        # Check target traceback frames
        target_frames = [f for f in execution_result.frames if f.is_target]
        if target_frames:
            # Last target frame is typically where the exception occurred
            return target_frames[-1].line_number, "runtime_frame"

    # 2. Check static analysis syntax diagnostics
    if analysis_report is not None:
        if analysis_report.syntax_diagnostics:
            return analysis_report.syntax_diagnostics[0].start_line, "syntax_error"
        if analysis_report.diagnostics:
            return analysis_report.diagnostics[0].start_line, "linter_diagnostic"

    return None, None


def _find_enclosing_function(
    ast_facts: ASTFacts | None,
    fault_line: int | None,
) -> ASTFunctionFact | None:
    """Find the innermost AST function or method containing fault_line."""
    if ast_facts is None or fault_line is None:
        return None

    matching: list[ASTFunctionFact] = []
    for func in ast_facts.functions:
        if func.start_line <= fault_line <= func.end_line:
            matching.append(func)

    if not matching:
        return None

    # Pick the innermost function (smallest line span)
    matching.sort(key=lambda f: f.end_line - f.start_line)
    return matching[0]


def _format_lines(lines: Sequence[str], start_line: int) -> str:
    """Format lines with 1-based line numbers."""
    return "\n".join(f"{start_line + i}: {line}" for i, line in enumerate(lines))


class ContextBuilder:
    """Builder for assembling compact, evidence-grounded model prompts within budget."""

    def __init__(
        self,
        config: LocaldevConfig | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.config = config or LocaldevConfig()
        self.token_counter = token_counter or (
            lambda text: estimate_tokens(text, chars_per_token=self.config.chars_per_token_heuristic)
        )

    def build_diagnosis_context(
        self,
        target: TargetRecord,
        source_text: str,
        analysis_report: AnalysisReport | None = None,
        execution_result: ExecutionResult | None = None,
        *,
        prompt_budget: int | None = None,
    ) -> BuiltPromptContext:
        """Assemble bounded diagnosis prompt strictly within prompt budget.

        Args:
            target: Target record with path and security metadata.
            source_text: Full source code text of the single target file.
            analysis_report: Optional static analysis report (syntax, AST, Ruff).
            execution_result: Optional runtime execution result (traceback, exit code).
            prompt_budget: Optional prompt token budget override (maximum 1,200).

        Returns:
            BuiltPromptContext containing prompts, metadata, evidence manifest, and truncation flags.

        Raises:
            ValueError: If prompt_budget exceeds MAX_ALLOWED_PROMPT_BUDGET (1,200).
            PromptBudgetExceededError: If minimal viable prompt exceeds prompt_budget.
        """
        budget = prompt_budget if prompt_budget is not None else self.config.prompt_budget_tokens
        if budget > MAX_ALLOWED_PROMPT_BUDGET:
            raise ValueError(
                f"prompt_budget ({budget}) cannot exceed hard limit of {MAX_ALLOWED_PROMPT_BUDGET}"
            )

        # Ensure budget partition invariant
        safety_margin = self.config.context_window_tokens - budget - self.config.output_budget_tokens
        if safety_margin < 0:
            raise ValueError(
                f"Budget partition invariant violated: {budget} (prompt) + "
                f"{self.config.output_budget_tokens} (output) > {self.config.context_window_tokens}"
            )

        system_prompt = DIAGNOSIS_SYSTEM_PROMPT
        included_categories: list[str] = ["system_and_instructions"]
        omitted_categories: list[str] = []
        was_truncated = False

        # Identify fault location and enclosing function
        fault_line, _ = _find_fault_location(analysis_report, execution_result)
        ast_facts = analysis_report.ast_facts if analysis_report else None
        enclosing_func = _find_enclosing_function(ast_facts, fault_line)
        fault_function_name = enclosing_func.qualified_name if enclosing_func else None

        source_lines = source_text.splitlines()
        total_lines = len(source_lines)

        # -------------------------------------------------------------------------
        # Priority 2: Primary Failure Signature & Manifest Registration
        # -------------------------------------------------------------------------
        primary_evidence: list[tuple[str, str]] = []
        failure_sig_lines: list[str] = []

        if execution_result is not None:
            if execution_result.error_signature:
                sig = execution_result.error_signature
                failure_sig_lines.append(
                    f"Runtime Exception: {sig.exception_type}: {sig.normalized_message}"
                )
                primary_evidence.append((
                    f"runtime:{sig.exception_type}",
                    f"Runtime exception {sig.exception_type}: {sig.normalized_message}",
                ))
                if sig.top_target_line:
                    failure_sig_lines.append(f"Fault Line in Target: line {sig.top_target_line}")
                    primary_evidence.append((
                        f"traceback:line_{sig.top_target_line}",
                        f"Top target traceback frame at line {sig.top_target_line}",
                    ))
            elif execution_result.exit_code != 0:
                failure_sig_lines.append(
                    f"Process exited with non-zero exit code {execution_result.exit_code}"
                )
                primary_evidence.append((
                    f"runtime:exit_code_{execution_result.exit_code}",
                    f"Non-zero subprocess exit code {execution_result.exit_code}",
                ))
            else:
                failure_sig_lines.append("Clean execution (exit code 0). No runtime exceptions.")
                primary_evidence.append((
                    "runtime:clean",
                    "Process exited cleanly with code 0",
                ))
        else:
            failure_sig_lines.append("No runtime execution evidence collected.")

        # Syntax facts
        if analysis_report is not None:
            if analysis_report.syntax_valid:
                primary_evidence.append((
                    "syntax:clean",
                    "Python syntax parsed cleanly without errors",
                ))
            else:
                for diag in analysis_report.syntax_diagnostics:
                    failure_sig_lines.append(
                        f"Syntax Error: {diag.message} at line {diag.start_line}, col {diag.start_col}"
                    )
                    primary_evidence.append((
                        f"syntax:error:line_{diag.start_line}",
                        f"Syntax error at line {diag.start_line}: {diag.message}",
                    ))

        included_categories.append("primary_failure_signature")

        # Traceback summary (target frames prioritized; external summarized compactly)
        target_tb_lines: list[str] = []
        external_tb_lines: list[str] = []
        target_frame_evidence: list[tuple[str, str]] = []
        if execution_result and execution_result.frames:
            for frame in execution_result.frames:
                if frame.is_target:
                    target_tb_lines.append(
                        f"  Line {frame.line_number} in {frame.function_name}(): {frame.code_line.strip()}"
                    )
                    target_frame_evidence.append((
                        f"traceback:line_{frame.line_number}",
                        f"Traceback frame in {frame.function_name} at line {frame.line_number}",
                    ))
                else:
                    file_name = Path(frame.file_path).name
                    external_tb_lines.append(
                        f"  [External Frame] {file_name}:{frame.line_number} in {frame.function_name}()"
                    )

        # -------------------------------------------------------------------------
        # Priority 3 & 5: Source Code Excerpt Definition
        # -------------------------------------------------------------------------
        if enclosing_func is not None:
            excerpt_start = enclosing_func.start_line
            excerpt_end = min(total_lines, enclosing_func.end_line)
        elif fault_line is not None:
            excerpt_start = max(1, fault_line - 15)
            excerpt_end = min(total_lines, fault_line + 15)
        else:
            excerpt_start = 1
            excerpt_end = min(total_lines, 30)

        included_categories.append("fault_excerpt")

        # -------------------------------------------------------------------------
        # Priority 4 & 6b: Ruff Diagnostics (excerpt vs secondary)
        # -------------------------------------------------------------------------
        excerpt_diags: list[DiagnosticRecord] = []
        secondary_diags: list[DiagnosticRecord] = []
        if analysis_report is not None:
            for d in analysis_report.diagnostics:
                if excerpt_start <= d.start_line <= excerpt_end:
                    excerpt_diags.append(d)
                else:
                    secondary_diags.append(d)

        # -------------------------------------------------------------------------
        # Priority 6a: Broader AST Outline Lines
        # -------------------------------------------------------------------------
        ast_outline_lines: list[str] = []
        if ast_facts is not None:
            if ast_facts.classes:
                class_names = [f"{c.name} (lines {c.start_line}-{c.end_line})" for c in ast_facts.classes]
                ast_outline_lines.append("Classes: " + ", ".join(class_names))
            if ast_facts.functions:
                other_funcs = [
                    f"{f.qualified_name} (lines {f.start_line}-{f.end_line})"
                    for f in ast_facts.functions
                    if enclosing_func is None or f.qualified_name != enclosing_func.qualified_name
                ]
                if other_funcs:
                    ast_outline_lines.append("Other Functions: " + ", ".join(other_funcs))

        # -------------------------------------------------------------------------
        # Iterative Budget Packing & Priority-Based Pruning
        # -------------------------------------------------------------------------
        def _render_user_prompt(
            current_excerpt_start: int,
            current_excerpt_end: int,
            include_tb_frames: bool,
            include_external_tb: bool,
            include_excerpt_diags: bool,
            include_ast_outline: bool,
            include_secondary_diags: bool,
            excerpt_prefix_note: str = "",
            excerpt_suffix_note: str = "",
        ) -> tuple[str, list[str]]:
            parts: list[str] = []
            manifest: list[str] = []
            manifest_descriptions: dict[str, str] = {}

            def _register(eid: str, desc: str) -> None:
                if eid not in manifest:
                    manifest.append(eid)
                    manifest_descriptions[eid] = desc

            # 1. Primary failure evidence
            for eid, desc in primary_evidence:
                _register(eid, desc)

            # 2. Target traceback frames
            if include_tb_frames:
                for eid, desc in target_frame_evidence:
                    _register(eid, desc)

            # 3. Excerpt AST functions and classes
            if enclosing_func is not None:
                _register(
                    f"ast:function:{enclosing_func.qualified_name}",
                    f"Function {enclosing_func.qualified_name} (lines {enclosing_func.start_line}-{enclosing_func.end_line})",
                )
            if ast_facts is not None:
                for fn in ast_facts.functions:
                    if fn.start_line <= current_excerpt_end and fn.end_line >= current_excerpt_start:
                        _register(
                            f"ast:function:{fn.qualified_name}",
                            f"Function {fn.qualified_name} (lines {fn.start_line}-{fn.end_line})",
                        )
                for cls in ast_facts.classes:
                    if cls.start_line <= current_excerpt_end and cls.end_line >= current_excerpt_start:
                        _register(
                            f"ast:class:{cls.name}",
                            f"Class {cls.name} (lines {cls.start_line}-{cls.end_line})",
                        )

            # 4. Source code excerpt
            _register(
                f"source:lines_{current_excerpt_start}-{current_excerpt_end}",
                f"Source lines {current_excerpt_start}-{current_excerpt_end} of target file",
            )

            # 5. Excerpt diagnostics
            if include_excerpt_diags:
                for d in excerpt_diags:
                    if current_excerpt_start <= d.start_line <= current_excerpt_end:
                        _register(
                            f"ruff:{d.code}:line_{d.start_line}",
                            f"Linter diagnostic {d.code} at line {d.start_line}: {d.message}",
                        )

            # 6. Secondary diagnostics (if included, capped at 3)
            if include_secondary_diags:
                for d in secondary_diags[:3]:
                    _register(
                        f"ruff:{d.code}:line_{d.start_line}",
                        f"Linter diagnostic {d.code} at line {d.start_line}: {d.message}",
                    )

            # 7. Broader AST outline (if included, capped at 3 each)
            if include_ast_outline and ast_facts is not None:
                outline_classes = [
                    c for c in ast_facts.classes
                    if not (c.start_line <= current_excerpt_end and c.end_line >= current_excerpt_start)
                ]
                for cls in outline_classes[:3]:
                    _register(
                        f"ast:class:{cls.name}",
                        f"Class {cls.name} (lines {cls.start_line}-{cls.end_line})",
                    )
                outline_funcs = [
                    f for f in ast_facts.functions
                    if (enclosing_func is None or f.qualified_name != enclosing_func.qualified_name)
                    and not (f.start_line <= current_excerpt_end and f.end_line >= current_excerpt_start)
                ]
                for fn in outline_funcs[:3]:
                    _register(
                        f"ast:function:{fn.qualified_name}",
                        f"Function {fn.qualified_name} (lines {fn.start_line}-{fn.end_line})",
                    )

            # Build text sections:
            # 1. Target Header
            parts.append(f"Target File: {target.path} ({total_lines} total lines)")

            # 2. Available Evidence Manifest
            parts.append("### Available Evidence Manifest (cite only IDs from this list):")
            for eid in manifest:
                desc = manifest_descriptions.get(eid, "")
                parts.append(f"- [{eid}]: {desc}" if desc else f"- [{eid}]")

            # 3. Primary Failure Evidence
            parts.append("### Primary Failure Evidence:")
            parts.extend(failure_sig_lines)
            if include_tb_frames:
                tb_lines: list[str] = list(target_tb_lines)
                if include_external_tb:
                    tb_lines.extend(external_tb_lines)
                if tb_lines:
                    parts.append("Traceback Summary:")
                    parts.extend(tb_lines)

            # 4. Target Source Code Excerpt
            parts.append("### Target Source Code Excerpt:")
            if total_lines > 0 and current_excerpt_start <= current_excerpt_end:
                slice_lines = source_lines[current_excerpt_start - 1 : current_excerpt_end]
                formatted_slice = _format_lines(slice_lines, current_excerpt_start)
                body_parts: list[str] = []
                if excerpt_prefix_note:
                    body_parts.append(excerpt_prefix_note)
                body_parts.append(formatted_slice)
                if excerpt_suffix_note:
                    body_parts.append(excerpt_suffix_note)
                excerpt_code = "\n".join(body_parts)
                parts.append(wrap_untrusted_code(excerpt_code))
            else:
                parts.append(wrap_untrusted_code("# [Empty source file]"))

            # 5. Excerpt Diagnostics
            if include_excerpt_diags and excerpt_diags:
                active_excerpt_diags = [
                    d for d in excerpt_diags
                    if current_excerpt_start <= d.start_line <= current_excerpt_end
                ]
                if active_excerpt_diags:
                    parts.append("### Linter Diagnostics in Excerpt:")
                    for d in active_excerpt_diags:
                        parts.append(f"- [line {d.start_line}] {d.source}/{d.code}: {d.message}")

            # 6. AST Outline
            if include_ast_outline and ast_outline_lines:
                parts.append("### Target Structural Outline (AST):")
                parts.extend(ast_outline_lines)

            # 7. Secondary Diagnostics
            if include_secondary_diags and secondary_diags:
                parts.append("### Secondary Diagnostics (outside excerpt):")
                for d in secondary_diags:
                    parts.append(f"- [line {d.start_line}] {d.source}/{d.code}: {d.message}")

            # 8. Final Instructions
            parts.append(
                "### Instructions:\n"
                "Analyze the provided evidence and source code to diagnose the defect.\n"
                "Output a single valid JSON object conforming strictly to DiagnosisRecord."
            )

            return "\n\n".join(parts), manifest

        # Start with all optional categories enabled
        inc_tb = True
        inc_external_tb = True
        inc_excerpt_diags = True
        inc_ast_outline = bool(ast_outline_lines)
        inc_secondary_diags = bool(secondary_diags)
        cur_start = excerpt_start
        cur_end = excerpt_end
        prefix_note = ""
        suffix_note = ""

        def _calc_tokens(usr_prompt: str) -> int:
            combined = f"{system_prompt}\n\n{usr_prompt}"
            return self.token_counter(combined)

        current_prompt, current_manifest = _render_user_prompt(
            cur_start,
            cur_end,
            inc_tb,
            inc_external_tb,
            inc_excerpt_diags,
            inc_ast_outline,
            inc_secondary_diags,
        )
        current_tokens = _calc_tokens(current_prompt)

        # ---------------------------------------------------------------------
        # Pruning pass 1: Secondary diagnostics (Priority 6b)
        # ---------------------------------------------------------------------
        if current_tokens > budget and inc_secondary_diags:
            inc_secondary_diags = False
            omitted_categories.append("secondary_diagnostics")
            was_truncated = True
            current_prompt, current_manifest = _render_user_prompt(
                cur_start,
                cur_end,
                inc_tb,
                inc_external_tb,
                inc_excerpt_diags,
                inc_ast_outline,
                inc_secondary_diags,
                prefix_note,
                suffix_note,
            )
            current_tokens = _calc_tokens(current_prompt)
        elif inc_secondary_diags:
            included_categories.append("secondary_diagnostics")

        # ---------------------------------------------------------------------
        # Pruning pass 2: Broader AST outline (Priority 6a)
        # ---------------------------------------------------------------------
        if current_tokens > budget and inc_ast_outline:
            inc_ast_outline = False
            omitted_categories.append("broader_ast_outline")
            was_truncated = True
            current_prompt, current_manifest = _render_user_prompt(
                cur_start,
                cur_end,
                inc_tb,
                inc_external_tb,
                inc_excerpt_diags,
                inc_ast_outline,
                inc_secondary_diags,
                prefix_note,
                suffix_note,
            )
            current_tokens = _calc_tokens(current_prompt)
        elif inc_ast_outline:
            included_categories.append("broader_ast_outline")

        # ---------------------------------------------------------------------
        # Pruning pass 3: External traceback frames
        # ---------------------------------------------------------------------
        if current_tokens > budget and inc_tb and inc_external_tb and bool(external_tb_lines):
            inc_external_tb = False
            omitted_categories.append("external_traceback_frames")
            was_truncated = True
            current_prompt, current_manifest = _render_user_prompt(
                cur_start,
                cur_end,
                inc_tb,
                inc_external_tb,
                inc_excerpt_diags,
                inc_ast_outline,
                inc_secondary_diags,
                prefix_note,
                suffix_note,
            )
            current_tokens = _calc_tokens(current_prompt)

        # ---------------------------------------------------------------------
        # Pruning pass 4: Shrink source excerpt around fault line (Priority 3 & 5)
        # ---------------------------------------------------------------------
        target_center = fault_line if fault_line is not None else (cur_start + cur_end) // 2
        original_start = cur_start
        original_end = cur_end

        # If source excerpt is larger than a narrow window, iteratively tighten it
        while current_tokens > budget and (cur_end - cur_start > 6):
            # Halve the window towards center
            left_span = max(3, (target_center - cur_start) // 2)
            right_span = max(3, (cur_end - target_center) // 2)
            new_start = max(1, target_center - left_span)
            new_end = min(total_lines, target_center + right_span)

            if new_start == cur_start and new_end == cur_end:
                break

            cur_start = new_start
            cur_end = new_end
            if cur_start > original_start:
                prefix_note = f"# ... [{cur_start - original_start} earlier lines omitted for budget] ..."
            if cur_end < original_end:
                suffix_note = f"# ... [{original_end - cur_end} later lines omitted for budget] ..."

            omitted_categories.append("function_body_excerpt")
            omitted_categories.append("surrounding_lines")
            was_truncated = True

            current_prompt, current_manifest = _render_user_prompt(
                cur_start,
                cur_end,
                inc_tb,
                inc_external_tb,
                inc_excerpt_diags,
                inc_ast_outline,
                inc_secondary_diags,
                prefix_note,
                suffix_note,
            )
            current_tokens = _calc_tokens(current_prompt)

        # ---------------------------------------------------------------------
        # Pruning pass 5: Excerpt diagnostics (Priority 4)
        # ---------------------------------------------------------------------
        if current_tokens > budget and inc_excerpt_diags:
            inc_excerpt_diags = False
            omitted_categories.append("excerpt_diagnostics")
            was_truncated = True
            current_prompt, current_manifest = _render_user_prompt(
                cur_start,
                cur_end,
                inc_tb,
                inc_external_tb,
                inc_excerpt_diags,
                inc_ast_outline,
                inc_secondary_diags,
                prefix_note,
                suffix_note,
            )
            current_tokens = _calc_tokens(current_prompt)
        elif inc_excerpt_diags and excerpt_diags:
            included_categories.append("excerpt_diagnostics")

        # ---------------------------------------------------------------------
        # Pruning pass 6: Traceback frames entirely
        # ---------------------------------------------------------------------
        if current_tokens > budget and inc_tb:
            inc_tb = False
            omitted_categories.append("traceback_summary")
            was_truncated = True
            current_prompt, current_manifest = _render_user_prompt(
                cur_start,
                cur_end,
                inc_tb,
                inc_external_tb,
                inc_excerpt_diags,
                inc_ast_outline,
                inc_secondary_diags,
                prefix_note,
                suffix_note,
            )
            current_tokens = _calc_tokens(current_prompt)

        # ---------------------------------------------------------------------
        # Pruning pass 7: Minimal source code (single line)
        # ---------------------------------------------------------------------
        if current_tokens > budget and cur_start < cur_end:
            cur_start = target_center
            cur_end = target_center
            prefix_note = "# ... [earlier lines omitted for budget] ..."
            suffix_note = "# ... [later lines omitted for budget] ..."
            current_prompt, current_manifest = _render_user_prompt(
                cur_start,
                cur_end,
                inc_tb,
                inc_external_tb,
                inc_excerpt_diags,
                inc_ast_outline,
                inc_secondary_diags,
                prefix_note,
                suffix_note,
            )
            current_tokens = _calc_tokens(current_prompt)

        # ---------------------------------------------------------------------
        # Final Verification: Hard upper limit enforcement
        # ---------------------------------------------------------------------
        if current_tokens > budget:
            raise PromptBudgetExceededError(
                f"Assembled prompt ({current_tokens} tokens) exceeds prompt budget ({budget} tokens) "
                "even after aggressive evidence pruning.",
                estimated_tokens=current_tokens,
                prompt_budget=budget,
            )

        # Deduplicate categories preserving order
        dedup_omitted = list(dict.fromkeys(omitted_categories))
        dedup_included = list(dict.fromkeys(included_categories))

        metadata = InferenceMetadata(
            context_window_tokens=self.config.context_window_tokens,
            prompt_budget_tokens=budget,
            output_budget_tokens=self.config.output_budget_tokens,
            application_safety_margin_tokens=safety_margin,
            estimated_prompt_tokens=current_tokens,
            was_truncated=was_truncated,
            omitted_evidence_categories=dedup_omitted,
        )

        return BuiltPromptContext(
            system_prompt=system_prompt,
            user_prompt=current_prompt,
            metadata=metadata,
            evidence_manifest=current_manifest,
            fault_line=fault_line,
            fault_function=fault_function_name,
            included_categories=dedup_included,
            omitted_categories=dedup_omitted,
            was_truncated=was_truncated,
        )


def build_diagnosis_context(
    target: TargetRecord,
    source_text: str,
    analysis_report: AnalysisReport | None = None,
    execution_result: ExecutionResult | None = None,
    *,
    config: LocaldevConfig | None = None,
    token_counter: Callable[[str], int] | None = None,
    prompt_budget: int | None = None,
) -> BuiltPromptContext:
    """Convenience functional interface for ContextBuilder.build_diagnosis_context."""
    builder = ContextBuilder(config=config, token_counter=token_counter)
    return builder.build_diagnosis_context(
        target=target,
        source_text=source_text,
        analysis_report=analysis_report,
        execution_result=execution_result,
        prompt_budget=prompt_budget,
    )
