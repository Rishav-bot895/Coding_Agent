"""Schema templates, token counters, prompt formatters, and validators for fine-tuning.

Defines:
- InstructionPair: Unified Pydantic container for instruction-tuning pairs.
- QwenTokenCounter: Qwen2.5 BPE tokenizer wrapper with local caching and offline fallback.
- Prompt builders mirroring ContextBuilder.to_messages().
- Strict Pydantic validators enforcing zero-markdown JSON and evidence grounding.
"""

from __future__ import annotations

import ast
import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from localdev.inference.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    EDIT_PROPOSAL_SYSTEM_PROMPT,
    UNTRUSTED_CODE_END,
    UNTRUSTED_CODE_START,
    sanitize_untrusted_code,
)
from localdev.patching.applier import PatchApplier
from localdev.patching.edit_schema import (
    EditProposalRecord,
)
from localdev.patching.edit_validator import EditValidator
from localdev.schemas import (
    DiagnosisAbstention,
    DiagnosisRecord,
)

logger = logging.getLogger(__name__)

# Hard limits mandated by Phase 13 token budget invariant
MAX_PROMPT_TOKENS: Final[int] = 1200
MAX_COMPLETION_TOKENS: Final[int] = 600
MAX_CONTEXT_WINDOW: Final[int] = 2048
DEFAULT_TOKENIZER_FILE: Final[Path] = Path(__file__).resolve().parent / "qwen_tokenizer.json"


class DatasetSampleType(StrEnum):
    """Supported task completion types in fine-tuning corpus."""

    DIAGNOSIS = "diagnosis"
    EDIT_PROPOSAL = "edit_proposal"
    ABSTENTION = "abstention"


class QwenTokenCounter:
    """Tokenizer wrapper for Qwen2.5-Coder using tokenizers library.

    Attempts to load from the local cached `qwen_tokenizer.json`.
    If absent, falls back to Hugging Face Hub (and caches locally),
    or uses an accurate character-level heuristic fallback if offline.
    """

    def __init__(self, tokenizer_path: Path | str | None = None) -> None:
        self.tokenizer_path = Path(tokenizer_path) if tokenizer_path else DEFAULT_TOKENIZER_FILE
        self._tokenizer: Any = None
        self._load_tokenizer()

    def _load_tokenizer(self) -> None:
        try:
            from tokenizers import Tokenizer  # type: ignore[import-untyped]

            if self.tokenizer_path.is_file():
                self._tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
                return

            # Try Hugging Face cache or download
            try:
                self._tokenizer = Tokenizer.from_pretrained("Qwen/Qwen2.5-Coder-1.5B-Instruct")
                self.tokenizer_path.parent.mkdir(parents=True, exist_ok=True)
                self._tokenizer.save(str(self.tokenizer_path))
            except Exception as exc:
                logger.warning("Failed to fetch Qwen tokenizer from Hub: %s. Using heuristic.", exc)
                self._tokenizer = None
        except ImportError:
            logger.warning("tokenizers package not installed. Using heuristic fallback.")
            self._tokenizer = None

    def count_tokens(self, text: str) -> int:
        """Count tokens for text using Qwen2.5 BPE tokenizer or heuristic."""
        if not text:
            return 0
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(text).ids)
        # Accurate BPE heuristic for Python code / JSON (~3.5 chars per token)
        return max(1, int(len(text) / 3.5 + 0.5))


class InstructionPair(BaseModel):
    """Single instruction-tuning sample ready for SFT / QLoRA training."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique deterministic sample identifier.")
    task_type: DatasetSampleType = Field(
        description="Task category: diagnosis, edit_proposal, or abstention."
    )
    category: str = Field(
        description="Sub-category (exception type, syntax error, logic bug, or abstention reason)."
    )
    target_file: str = Field(description="Target file basename or relative path.")
    source_code: str = Field(description="Full source code before repair.")
    prompt: str = Field(description="Combined prompt string (system + user).")
    completion: str = Field(description="Raw JSON target completion (zero markdown code blocks).")
    messages: list[dict[str, str]] = Field(
        description="Standard chat format: system, user, and assistant messages."
    )
    prompt_tokens: int = Field(ge=1, le=MAX_PROMPT_TOKENS, description="Prompt token count.")
    completion_tokens: int = Field(
        ge=1, le=MAX_COMPLETION_TOKENS, description="Target completion token count."
    )
    total_tokens: int = Field(
        ge=2, le=MAX_CONTEXT_WINDOW, description="Combined total token count."
    )
    evidence_manifest: list[str] = Field(
        default_factory=list, description="Grounding evidence IDs present in prompt."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Diagnostic, fault location, or line metadata."
    )


def format_source_lines(source_code: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Format 1-based numbered source lines within range."""
    lines = source_code.splitlines()
    total = len(lines)
    end = total if end_line is None else min(end_line, total)
    start = max(1, min(start_line, end))
    numbered = [f"{start + i}: {lines[start - 1 + i]}" for i in range(end - start + 1)]
    return "\n".join(numbered)


def build_diagnosis_prompt_messages(
    target_file: str,
    source_code: str,
    evidence_items: list[tuple[str, str]],
    failure_lines: list[str],
    excerpt_start: int = 1,
    excerpt_end: int | None = None,
) -> tuple[list[dict[str, str]], str, list[str]]:
    """Build standardized diagnosis prompt mirroring ContextBuilder.to_messages().

    Returns:
        (messages, user_prompt, evidence_manifest_ids)
    """
    total_lines = len(source_code.splitlines())
    end = total_lines if excerpt_end is None else min(excerpt_end, total_lines)
    manifest_ids = [eid for eid, _ in evidence_items]

    parts: list[str] = []
    # 1. Target Header
    parts.append(f"Target File: {target_file} ({total_lines} total lines)")

    # 2. Available Evidence Manifest
    parts.append("### Available Evidence Manifest (cite only IDs from this list):")
    for eid, desc in evidence_items:
        parts.append(f"- [{eid}]: {desc}" if desc else f"- [{eid}]")

    # 3. Primary Failure Evidence
    parts.append("### Primary Failure Evidence:")
    parts.extend(failure_lines)

    # 4. Source code excerpt wrapped in containment markers
    formatted_code = format_source_lines(source_code, excerpt_start, end)
    sanitized = sanitize_untrusted_code(formatted_code)
    parts.append(f"### Target Source Code Excerpt (lines {excerpt_start}-{end}):")
    parts.append(f"{UNTRUSTED_CODE_START}\n{sanitized}\n{UNTRUSTED_CODE_END}")

    # 5. Instructions
    parts.append(
        "### Instructions:\n"
        "Analyze the provided evidence and source code to diagnose the defect.\n"
        "Output a single valid JSON object conforming strictly to DiagnosisRecord."
    )

    user_prompt = "\n\n".join(parts)
    messages = [
        {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return messages, user_prompt, manifest_ids


def build_edit_proposal_prompt_messages(
    target_file: str,
    source_code: str,
    diagnosis_summary: str | None = None,
    failure_evidence: list[str] | None = None,
    excerpt_start: int = 1,
    excerpt_end: int | None = None,
) -> tuple[list[dict[str, str]], str]:
    """Build standardized edit proposal prompt mirroring ContextBuilder.to_messages().

    Returns:
        (messages, user_prompt)
    """
    total_lines = len(source_code.splitlines())
    end = total_lines if excerpt_end is None else min(excerpt_end, total_lines)

    parts: list[str] = []
    # 1. Target Header
    parts.append(f"Target File: {target_file} ({total_lines} total lines)")

    # 2. Diagnosis summary if provided
    if diagnosis_summary:
        parts.append("### Bug Diagnosis Context:")
        parts.append(diagnosis_summary)

    # 3. Primary Failure Evidence
    if failure_evidence:
        parts.append("### Primary Failure Evidence:")
        parts.extend(failure_evidence)

    # 4. Source code excerpt wrapped in containment markers
    formatted_code = format_source_lines(source_code, excerpt_start, end)
    sanitized = sanitize_untrusted_code(formatted_code)
    parts.append(f"### Target Source Code Excerpt (lines {excerpt_start}-{end}):")
    parts.append(f"{UNTRUSTED_CODE_START}\n{sanitized}\n{UNTRUSTED_CODE_END}")

    # 5. Instructions
    parts.append(
        "### Instructions:\n"
        "Propose a minimal, surgical patch fixing the identified defect.\n"
        "Output a single valid JSON object conforming strictly to EditProposalRecord."
    )

    user_prompt = "\n\n".join(parts)
    messages = [
        {"role": "system", "content": EDIT_PROPOSAL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return messages, user_prompt


def build_abstention_prompt_messages(
    target_file: str,
    source_code: str,
    evidence_items: list[tuple[str, str]],
    failure_lines: list[str],
    excerpt_start: int = 1,
    excerpt_end: int | None = None,
) -> tuple[list[dict[str, str]], str, list[str]]:
    """Build standardized abstention prompt (clean file or boundary violation).

    Returns:
        (messages, user_prompt, evidence_manifest_ids)
    """
    total_lines = len(source_code.splitlines())
    end = total_lines if excerpt_end is None else min(excerpt_end, total_lines)
    manifest_ids = [eid for eid, _ in evidence_items]

    parts: list[str] = []
    parts.append(f"Target File: {target_file} ({total_lines} total lines)")

    parts.append("### Available Evidence Manifest (cite only IDs from this list):")
    for eid, desc in evidence_items:
        parts.append(f"- [{eid}]: {desc}" if desc else f"- [{eid}]")

    parts.append("### Primary Failure Evidence:")
    parts.extend(failure_lines)

    formatted_code = format_source_lines(source_code, excerpt_start, end)
    sanitized = sanitize_untrusted_code(formatted_code)
    parts.append(f"### Target Source Code Excerpt (lines {excerpt_start}-{end}):")
    parts.append(f"{UNTRUSTED_CODE_START}\n{sanitized}\n{UNTRUSTED_CODE_END}")

    parts.append(
        "### Instructions:\n"
        "Analyze the provided evidence and source code.\n"
        "If the code has no defect, or the failure is outside single-target file boundaries,\n"
        "output a single valid JSON object conforming strictly to DiagnosisAbstention."
    )

    user_prompt = "\n\n".join(parts)
    messages = [
        {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return messages, user_prompt, manifest_ids


# =============================================================================
# Validation and Schema Enforcement Functions
# =============================================================================


def validate_raw_json_no_markdown(text: str) -> tuple[bool, Any, list[str]]:
    """Verify that string is raw JSON and contains NO markdown fences or commentary."""
    stripped = text.strip()
    errors: list[str] = []

    if "```" in stripped:
        errors.append("Output contains markdown code fences (```). Raw JSON required.")
        return False, None, errors

    if not stripped.startswith("{") or not stripped.endswith("}"):
        errors.append(
            "Output must begin with '{' and end with '}'. "
            "No conversational preamble/postamble allowed."
        )
        return False, None, errors

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        errors.append(f"Malformed JSON syntax: {exc}")
        return False, None, errors

    if not isinstance(parsed, dict):
        errors.append(f"Expected top-level JSON object, got {type(parsed).__name__}")
        return False, None, errors

    return True, parsed, []


def validate_diagnosis_completion(
    completion: str,
    evidence_manifest: list[str],
) -> tuple[bool, DiagnosisRecord | None, list[str]]:
    """Validate diagnosis completion against DiagnosisRecord and evidence manifest."""
    is_raw_json, parsed_json, errors = validate_raw_json_no_markdown(completion)
    if not is_raw_json:
        return False, None, errors

    try:
        record = DiagnosisRecord.model_validate(parsed_json)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            errors.append(f"Schema violation at '{loc}': {err['msg']}")
        return False, None, errors

    # Check evidence grounding: all cited IDs must exist in the manifest
    manifest_set = set(evidence_manifest)
    ungrounded: list[str] = []
    for eid in record.cited_evidence_ids:
        if eid not in manifest_set:
            ungrounded.append(eid)

    if ungrounded:
        errors.append(
            f"Ungrounded evidence cited: {ungrounded}. Available IDs: {sorted(manifest_set)}"
        )
        return False, None, errors

    if not record.bug_description.strip():
        errors.append("bug_description cannot be empty.")
        return False, None, errors

    if not record.root_cause.strip():
        errors.append("root_cause cannot be empty.")
        return False, None, errors

    return True, record, []


def validate_edit_completion(
    completion: str,
    source_code: str,
    target_file: str,
) -> tuple[bool, EditProposalRecord | None, list[str]]:
    """Validate edit completion against EditProposalRecord, source text, and patch application."""
    is_raw_json, parsed_json, errors = validate_raw_json_no_markdown(completion)
    if not is_raw_json:
        return False, None, errors

    try:
        proposal = EditProposalRecord.model_validate(parsed_json)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            errors.append(f"Schema violation at '{loc}': {err['msg']}")
        return False, None, errors

    # Validate target filename
    if proposal.target_file != target_file:
        errors.append(
            f"Target file mismatch: proposal has '{proposal.target_file}', expected '{target_file}'"
        )
        return False, None, errors

    # Validate edit boundaries and expected_text match using EditValidator
    validator = EditValidator(target=target_file, source_text=source_code)
    val_res = validator.validate_proposal(proposal)
    if not val_res.is_valid:
        errors.extend(val_res.errors)
        return False, None, errors

    # Verify patch applies cleanly and patched code parses as valid Python
    applier = PatchApplier()
    try:
        patched_lines = applier.apply_to_lines(source_code.splitlines(), proposal.edits)
        patched_code = "\n".join(patched_lines)
        ast.parse(patched_code)
    except Exception as exc:
        errors.append(f"Patch application or AST verification failed: {exc}")
        return False, None, errors

    return True, proposal, []


def validate_abstention_completion(
    completion: str,
    target_file: str,
) -> tuple[bool, DiagnosisAbstention | None, list[str]]:
    """Validate abstention completion against DiagnosisAbstention schema."""
    is_raw_json, parsed_json, errors = validate_raw_json_no_markdown(completion)
    if not is_raw_json:
        return False, None, errors

    try:
        abstention = DiagnosisAbstention.model_validate(parsed_json)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            errors.append(f"Schema violation at '{loc}': {err['msg']}")
        return False, None, errors

    if abstention.target != target_file:
        errors.append(
            f"Target mismatch: abstention has '{abstention.target}', expected '{target_file}'"
        )
        return False, None, errors

    if not abstention.details.strip():
        errors.append("details cannot be empty.")
        return False, None, errors

    return True, abstention, []


def validate_instruction_pair(
    pair: InstructionPair,
    token_counter: QwenTokenCounter | None = None,
) -> tuple[bool, list[str]]:
    """End-to-end multi-stage validation for an instruction pair.

    Enforces:
    1. Prompt token length <= 1,200 tokens
    2. Completion token length <= 600 tokens
    3. Total token length <= 2,048 tokens
    4. Chat format consistency (system, user, assistant)
    5. Raw JSON without markdown fences
    6. Task-specific schema compliance:
       - Diagnosis: DiagnosisRecord + strict evidence grounding
       - Edit Proposal: EditProposalRecord + exact text match + clean patch application
       - Abstention: DiagnosisAbstention + valid reason code
    """
    errors: list[str] = []

    # 1. Token budget invariants
    if pair.prompt_tokens > MAX_PROMPT_TOKENS:
        errors.append(
            f"Prompt tokens ({pair.prompt_tokens}) exceeds maximum allowed ({MAX_PROMPT_TOKENS})"
        )
    if pair.completion_tokens > MAX_COMPLETION_TOKENS:
        errors.append(
            f"Completion tokens ({pair.completion_tokens}) exceeds maximum "
            f"allowed ({MAX_COMPLETION_TOKENS})"
        )
    if pair.total_tokens > MAX_CONTEXT_WINDOW:
        errors.append(
            f"Total tokens ({pair.total_tokens}) exceeds context window ({MAX_CONTEXT_WINDOW})"
        )
    if pair.prompt_tokens + pair.completion_tokens != pair.total_tokens:
        errors.append(
            f"Token arithmetic mismatch: {pair.prompt_tokens} + "
            f"{pair.completion_tokens} != {pair.total_tokens}"
        )

    # Re-verify token counts if counter provided
    if token_counter is not None:
        expected_prompt_tokens = token_counter.count_tokens(pair.prompt)
        expected_completion_tokens = token_counter.count_tokens(pair.completion)
        if abs(expected_prompt_tokens - pair.prompt_tokens) > 2:
            errors.append(
                f"Prompt token count discrepancy: recorded {pair.prompt_tokens}, "
                f"counter measured {expected_prompt_tokens}"
            )
        if abs(expected_completion_tokens - pair.completion_tokens) > 2:
            errors.append(
                f"Completion token count discrepancy: recorded {pair.completion_tokens}, "
                f"counter measured {expected_completion_tokens}"
            )

    # 2. Chat messages structure
    if len(pair.messages) != 3:
        errors.append(f"Expected 3 messages (system, user, assistant), got {len(pair.messages)}")
    else:
        if pair.messages[0].get("role") != "system":
            errors.append(
                f"First message role must be 'system', got {pair.messages[0].get('role')}"
            )
        if pair.messages[1].get("role") != "user":
            errors.append(f"Second message role must be 'user', got {pair.messages[1].get('role')}")
        if pair.messages[2].get("role") != "assistant":
            errors.append(
                f"Third message role must be 'assistant', got {pair.messages[2].get('role')}"
            )

    # 3. Task specific schema validation
    if pair.task_type == DatasetSampleType.DIAGNOSIS:
        ok, _, diag_errs = validate_diagnosis_completion(pair.completion, pair.evidence_manifest)
        if not ok:
            errors.extend(diag_errs)
    elif pair.task_type == DatasetSampleType.EDIT_PROPOSAL:
        ok, _, edit_errs = validate_edit_completion(
            pair.completion, pair.source_code, pair.target_file
        )
        if not ok:
            errors.extend(edit_errs)
    elif pair.task_type == DatasetSampleType.ABSTENTION:
        ok, _, abst_errs = validate_abstention_completion(pair.completion, pair.target_file)
        if not ok:
            errors.extend(abst_errs)
    else:
        errors.append(f"Unknown task type: {pair.task_type}")

    return len(errors) == 0, errors
