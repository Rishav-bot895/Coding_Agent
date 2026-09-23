"""Strongly typed, versioned internal and external Pydantic schemas for localdev.

Provides schema validation, JSON serialization, and Ollama-compatible JSON Schema
export (.model_json_schema()) for all deterministic evidence, model proposals,
validation reports, complexity estimations, profiling data, and top-level envelopes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from localdev.constants import (
    APPLICATION_SAFETY_MARGIN_TOKENS,
    CONTEXT_WINDOW_TOKENS,
    MAX_PATCH_CHANGED_LINES,
    MAX_PATCH_EDITS,
    OUTPUT_BUDGET_TOKENS,
    PROMPT_BUDGET_TOKENS,
)

T = TypeVar("T")


# =============================================================================
# Enums
# =============================================================================


class ConfidenceEnum(str, Enum):
    """Confidence levels for diagnosis and complexity analysis."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ComplexityClassEnum(str, Enum):
    """Closed vocabulary of asymptotic Big-O complexity classes."""

    O_1 = "O(1)"
    O_LOG_N = "O(log n)"
    O_N = "O(n)"
    O_N_LOG_N = "O(n log n)"
    O_N2 = "O(n²)"
    O_N3 = "O(n³)"
    O_NM = "O(nm)"
    O_2N = "O(2^n)"
    UNKNOWN = "UNKNOWN"


class ComplexityAbstentionReason(str, Enum):
    """Reason codes for complexity analysis abstention."""

    UNKNOWN_CALL = "UNKNOWN_CALL"
    DYNAMIC_BOUNDS = "DYNAMIC_BOUNDS"
    DYNAMIC_RECURSION = "DYNAMIC_RECURSION"
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"
    UNSUPPORTED_SYNTAX = "UNSUPPORTED_SYNTAX"


class EditOperationType(str, Enum):
    """Supported single-file edit operation types."""

    REPLACE = "replace"
    INSERT = "insert"
    DELETE = "delete"


class SeverityEnum(str, Enum):
    """Diagnostic severity levels."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationLevel(str, Enum):
    """Empirical patch validation tiers."""

    NONE = "NONE"
    LEVEL_A = "A"  # Static validity (parses cleanly, zero new diagnostics)
    LEVEL_B = "B"  # Failure reproduction removed (does NOT prove bug fixed)
    LEVEL_C = "C"  # Clean execution (exit code 0 under controlled runtime)
    LEVEL_D = "D"  # Verified behavioral correctness (oracle satisfied)


class DetectionConfidence(str, Enum):
    """Confidence scoring for target language detection."""

    CERTAIN = "CERTAIN"
    PROBABLE = "PROBABLE"
    UNSUPPORTED = "UNSUPPORTED"


# =============================================================================
# Target & Diagnostics Schemas
# =============================================================================


class LanguageCapabilities(BaseModel):
    """Static capability declaration for a language adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supports_syntax_check: bool = Field(
        default=True, description="Whether adapter provides syntax verification."
    )
    supports_ast_facts: bool = Field(
        default=True, description="Whether adapter extracts structural AST facts."
    )
    supports_diagnostics: bool = Field(
        default=True, description="Whether adapter runs static linter diagnostics."
    )
    supports_execution: bool = Field(
        default=True, description="Whether adapter prepares controlled subprocess execution."
    )
    supports_complexity: bool = Field(
        default=True, description="Whether adapter provides static complexity bounds."
    )
    supports_validation: bool = Field(
        default=True, description="Whether adapter validates candidate patches across tiers A-D."
    )


class DetectionResult(BaseModel):
    """Result of non-executing target language detection."""

    model_config = ConfigDict(extra="forbid")

    language: str = Field(description="Detected language identifier (e.g. 'python', 'unsupported').")
    confidence: DetectionConfidence = Field(description="Detection confidence tier.")
    reasons: list[str] = Field(
        default_factory=list, description="Signals supporting detection classification."
    )
    matched_extension: str | None = Field(
        default=None, description="Matched file extension if any."
    )
    has_shebang: bool = Field(
        default=False, description="Whether a valid shebang line was detected."
    )


class TargetRecord(BaseModel):
    """Immutable record of resolved target file attributes and security metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(description="Target path as supplied or normalized.")
    absolute_path: str = Field(description="Resolved canonical absolute path on Windows.")
    file_size_bytes: int = Field(ge=0, description="Size of target file in bytes.")
    sha256: str = Field(min_length=64, max_length=64, description="Pre-edit SHA-256 baseline hash.")
    encoding: str = Field(default="utf-8", description="Detected PEP 263 source encoding.")
    has_bom: bool = Field(default=False, description="Whether UTF-8 BOM was detected.")
    newline_style: Literal["\r\n", "\n", "mixed"] = Field(
        default="\n", description="Detected source newline convention."
    )
    has_trailing_newline: bool = Field(default=True, description="Whether file ends with newline.")
    is_read_only: bool = Field(default=False, description="Windows read-only attribute status.")
    is_reparse_point: bool = Field(
        default=False, description="Whether target is a symlink or NTFS junction."
    )


class TargetInfoRecord(BaseModel):
    """Structured data payload for 'localdev info' command."""

    model_config = ConfigDict(extra="forbid")

    target: TargetRecord = Field(description="Target file attributes and security metadata.")
    total_lines: int = Field(ge=0, description="Total line count in target file.")
    detection: DetectionResult = Field(description="Non-executing language detection result.")


class DiagnosticRecord(BaseModel):
    """Normalized static analysis diagnostic (from Ruff, syntax check, or AST)."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Diagnostic provider: ruff, python_syntax, or static.")
    code: str = Field(description="Diagnostic or rule code (e.g., E501, SyntaxError).")
    message: str = Field(description="Normalized diagnostic message.")
    severity: SeverityEnum = Field(default=SeverityEnum.ERROR, description="Diagnostic severity.")
    start_line: int = Field(ge=1, description="1-based inclusive starting line.")
    start_col: int = Field(ge=1, description="1-based starting column.")
    end_line: int = Field(ge=1, description="1-based inclusive ending line.")
    end_col: int = Field(ge=1, description="1-based ending column.")
    fix_available: bool = Field(default=False, description="Whether an automated fix exists.")

    @model_validator(mode="after")
    def validate_line_range(self) -> DiagnosticRecord:
        if self.start_line > self.end_line:
            raise ValueError(f"start_line ({self.start_line}) cannot be greater than end_line ({self.end_line})")
        if self.start_line == self.end_line and self.start_col > self.end_col:
            raise ValueError(f"start_col ({self.start_col}) cannot be greater than end_col ({self.end_col}) on same line")
        return self


# =============================================================================
# Execution & Traceback Schemas
# =============================================================================


class TracebackFrame(BaseModel):
    """Structured frame extracted from a Python execution traceback."""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Normalized canonical file path.")
    line_number: int = Field(ge=1, description="1-based line number.")
    function_name: str = Field(description="Function or scope name.")
    code_line: str = Field(default="", description="Source code text at line.")
    is_target: bool = Field(
        default=False, description="True if frame points to the explicit target file."
    )


class ErrorSignature(BaseModel):
    """Normalized runtime failure signature for Level B reproduction testing."""

    model_config = ConfigDict(extra="forbid")

    exception_type: str = Field(description="Name of exception class (e.g. ZeroDivisionError).")
    normalized_message: str = Field(description="Sanitized exception message string.")
    top_target_file: str | None = Field(
        default=None, description="Path of highest target frame in traceback."
    )
    top_target_line: int | None = Field(
        default=None, ge=1, description="Line of highest target frame in traceback."
    )


class ExecutionSpec(BaseModel):
    """Prepared execution specification for running a target subprocess."""

    model_config = ConfigDict(extra="forbid")

    command_args: list[str] = Field(
        min_length=1, description="Command arguments to execute."
    )
    env_overrides: dict[str, str] = Field(
        default_factory=dict, description="Environment variable overrides."
    )
    cwd: str | None = Field(
        default=None, description="Working directory for subprocess execution."
    )


class ExecutionResult(BaseModel):
    """Result of controlled target subprocess execution under -E -B -P."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int = Field(description="Subprocess return code.")
    stdout: str = Field(default="", description="Captured stdout string.")
    stderr: str = Field(default="", description="Captured stderr string.")
    duration_seconds: float = Field(ge=0.0, description="Wall-clock elapsed execution time.")
    timed_out: bool = Field(default=False, description="True if process was killed due to timeout.")
    output_truncated: bool = Field(
        default=False, description="True if output was truncated due to byte cap."
    )
    peak_process_tree_rss_bytes: int | None = Field(
        default=None, ge=0, description="Approximate peak RSS of process tree."
    )
    execution_backend: Literal["windows_job", "psutil_fallback"] = Field(
        default="windows_job", description="Active process containment backend."
    )
    error_signature: ErrorSignature | None = Field(
        default=None, description="Parsed failure signature if execution failed."
    )
    frames: list[TracebackFrame] = Field(
        default_factory=list, description="Parsed traceback frames in order."
    )


# =============================================================================
# AST Facts
# =============================================================================


class ASTFunctionFact(BaseModel):
    """Metadata for a function or method extracted from AST."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Simple function name.")
    qualified_name: str = Field(description="Dotted name (e.g., ClassName.method_name).")
    start_line: int = Field(ge=1, description="1-based inclusive start line.")
    end_line: int = Field(ge=1, description="1-based inclusive end line.")
    parameters: list[str] = Field(default_factory=list, description="Parameter names.")
    is_async: bool = Field(default=False, description="True if async function.")
    is_method: bool = Field(default=False, description="True if defined inside a class.")
    docstring: str | None = Field(default=None, description="Extracted docstring if present.")


class ASTClassFact(BaseModel):
    """Metadata for a class extracted from AST."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Class name.")
    start_line: int = Field(ge=1, description="1-based inclusive start line.")
    end_line: int = Field(ge=1, description="1-based inclusive end line.")
    methods: list[str] = Field(default_factory=list, description="Method names in class.")


class ASTFacts(BaseModel):
    """Bounded structural facts extracted from a valid Python AST."""

    model_config = ConfigDict(extra="forbid")

    functions: list[ASTFunctionFact] = Field(default_factory=list)
    classes: list[ASTClassFact] = Field(default_factory=list)
    total_lines: int = Field(ge=0, description="Total line count in source file.")
    syntax_error: str | None = Field(
        default=None, description="Syntax error message if source failed to parse."
    )


# =============================================================================
# Local SLM Production Schemas (Ollama Grammar-Constrained Decoding)
# =============================================================================


class DiagnosisRecord(BaseModel):
    """Evidence-grounded bug diagnosis generated by local SLM via Ollama.

    This schema is intentionally simple and flat for reliable token-constrained
    decoding against Ollama's format parameter.
    """

    model_config = ConfigDict(extra="forbid")

    bug_description: str = Field(
        description="Concise description of the diagnosed defect or behavior."
    )
    root_cause: str = Field(
        description="Explanation of the underlying root cause identified from evidence."
    )
    confidence: ConfidenceEnum = Field(
        description="Confidence level in the diagnosis: HIGH, MEDIUM, or LOW."
    )
    cited_evidence_ids: list[str] = Field(
        default_factory=list,
        description="List of verified evidence IDs directly supporting this diagnosis.",
    )
    rationale: str = Field(
        description="Logical reasoning linking cited evidence to the proposed cause."
    )


class EditOperation(BaseModel):
    """Single line-oriented edit operation within a single source file."""

    model_config = ConfigDict(extra="forbid")

    operation: EditOperationType = Field(
        description="Operation type: replace, insert, or delete."
    )
    start_line: int = Field(ge=1, description="1-based inclusive starting line.")
    end_line: int = Field(ge=1, description="1-based inclusive ending line.")
    expected_text: str = Field(
        description="Normalized text expected in target file at specified line range."
    )
    replacement_text: str = Field(
        default="",
        description="New replacement text to insert. Empty for delete operation.",
    )

    @model_validator(mode="after")
    def validate_operation_lines(self) -> EditOperation:
        if (
            self.operation in (EditOperationType.REPLACE, EditOperationType.DELETE)
            and self.start_line > self.end_line
        ):
            raise ValueError(
                f"{self.operation.value} operation requires start_line ({self.start_line}) <= end_line ({self.end_line})"
            )
        if self.operation == EditOperationType.INSERT and self.start_line != self.end_line:
            raise ValueError(
                f"insert operation requires start_line == end_line, got {self.start_line} and {self.end_line}"
            )
        return self


class EditProposalRecord(BaseModel):
    """Structured patch proposal containing validated line edits for one target file.

    Enforces 1-based inclusive line indexing, bounded edit counts, and path safety.
    """

    model_config = ConfigDict(extra="forbid")

    target_file: str = Field(
        description="Relative or canonical name of the single target file."
    )
    edits: list[EditOperation] = Field(
        min_length=1,
        max_length=MAX_PATCH_EDITS,
        description=f"List of structured edits (maximum {MAX_PATCH_EDITS}).",
    )
    explanation: str = Field(description="Summary of the rationale for this patch.")

    @field_validator("target_file")
    @classmethod
    def validate_target_path_safety(cls, v: str) -> str:
        # Prevent path traversal and shell injection
        if ".." in v or ("/" in v and "\\" in v):
            raise ValueError(f"Path traversal or mixed separators forbidden in target_file: {v}")
        for char in ("&", "|", ";", ">", "<", "`", "$"):
            if char in v:
                raise ValueError(f"Shell metacharacters forbidden in target_file: {v}")
        return v

    @model_validator(mode="after")
    def validate_patch_limits(self) -> EditProposalRecord:
        total_changed_lines = 0
        for edit in self.edits:
            exp_lines = len(edit.expected_text.splitlines()) if edit.expected_text else 0
            rep_lines = len(edit.replacement_text.splitlines()) if edit.replacement_text else 0
            total_changed_lines += max(exp_lines, rep_lines)

        if total_changed_lines > MAX_PATCH_CHANGED_LINES:
            raise ValueError(
                f"Total changed lines ({total_changed_lines}) exceeds hard limit of {MAX_PATCH_CHANGED_LINES}"
            )
        return self


# =============================================================================
# Validation Reports (Levels A–D)
# =============================================================================


class ValidationReport(BaseModel):
    """Empirical validation result evaluating candidate code across Levels A–D."""

    model_config = ConfigDict(extra="forbid")

    level_achieved: ValidationLevel = Field(
        description="Highest empirical validation tier achieved by candidate."
    )
    static_valid: bool = Field(
        description="Level A: AST parses cleanly and zero new Ruff diagnostics introduced."
    )
    failure_reproduction_removed: bool = Field(
        description="Level B: Original runtime failure signature is no longer observed."
    )
    clean_execution: bool = Field(
        description="Level C: Candidate exits successfully with code 0 under controlled limits."
    )
    behavioral_oracle_passed: bool | None = Field(
        default=None,
        description="Level D: User-supplied behavioral assertions (--expected-stdout/exit) satisfied.",
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Detailed diagnostic metrics and check results."
    )


# =============================================================================
# Complexity Report (CPython Semantics)
# =============================================================================


class ComplexityReport(BaseModel):
    """Static algorithmic complexity bounds grounded in CPython runtime semantics."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="Target file path or selector (file.py::func).")
    time_complexity: ComplexityClassEnum = Field(description="Time complexity class.")
    auxiliary_space: ComplexityClassEnum = Field(
        description="Transient intermediate space complexity (stack, heap buffers)."
    )
    output_space: ComplexityClassEnum = Field(
        description="Space escaping execution as return values."
    )
    confidence: ConfidenceEnum = Field(description="Confidence in estimated bound.")
    is_amortized: bool = Field(
        default=False, description="True if bound depends on amortized costs (e.g. list.append)."
    )
    is_expected: bool = Field(
        default=False, description="True if bound is expected/average case rather than worst-case."
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Explicit static assumptions required for this bound."
    )
    abstention_reason: ComplexityAbstentionReason | None = Field(
        default=None, description="Abstention code if complexity is UNKNOWN."
    )
    details: str = Field(default="", description="Human-readable explanation of derivations.")


# =============================================================================
# Profiling Report (Hot-Process & Separated Metrics)
# =============================================================================


class ProfileReport(BaseModel):
    """Function profiling report separating import costs, latency, heap, and RSS."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="Target file path and function selector.")
    import_duration_ms: float = Field(ge=0.0, description="Time to load module via direct path.")
    import_stdout: str = Field(default="", description="Output emitted during module import.")
    import_stderr: str = Field(default="", description="Errors emitted during module import.")
    warmup_invocations: int = Field(ge=0, description="Number of warm-up iterations executed.")
    measured_invocations: int = Field(ge=1, description="Number of measured iterations timed.")
    median_latency_ms: float = Field(ge=0.0, description="Median invocation latency in ms.")
    mean_latency_ms: float = Field(ge=0.0, description="Mean invocation latency in ms.")
    min_latency_ms: float = Field(ge=0.0, description="Minimum invocation latency in ms.")
    max_latency_ms: float = Field(ge=0.0, description="Maximum invocation latency in ms.")
    dispersion_ms: float = Field(ge=0.0, description="Latency dispersion (standard deviation).")
    python_allocations_tracemalloc_bytes: int = Field(
        ge=0, description="Peak tracemalloc-tracked Python heap allocations."
    )
    approximate_process_tree_rss_bytes: int = Field(
        ge=0, description="Approximate peak RSS of the profiling worker process tree."
    )
    hot_process_reused: bool = Field(
        default=True,
        description="True if measurements ran in hot process with persistent module state.",
    )


# =============================================================================
# Inference Metadata Schema
# =============================================================================


class InferenceMetadata(BaseModel):
    """Context budget tracking and Ollama usage metrics."""

    model_config = ConfigDict(extra="forbid")

    context_window_tokens: int = Field(
        default=CONTEXT_WINDOW_TOKENS, description="Hard context ceiling (2,048)."
    )
    prompt_budget_tokens: int = Field(
        default=PROMPT_BUDGET_TOKENS, description="Application prompt budget (1,200)."
    )
    output_budget_tokens: int = Field(
        default=OUTPUT_BUDGET_TOKENS, description="Output prediction budget (600)."
    )
    application_safety_margin_tokens: int = Field(
        default=APPLICATION_SAFETY_MARGIN_TOKENS, description="Safety margin overhead (248)."
    )
    estimated_prompt_tokens: int = Field(ge=0, description="Estimated prompt token count.")
    prompt_eval_count: int | None = Field(
        default=None, ge=0, description="Actual prompt tokens evaluated by Ollama."
    )
    eval_count: int | None = Field(
        default=None, ge=0, description="Actual tokens generated by Ollama."
    )
    prompt_eval_duration_ms: float | None = Field(
        default=None, ge=0.0, description="Duration of prompt evaluation in ms."
    )
    eval_duration_ms: float | None = Field(
        default=None, ge=0.0, description="Duration of generation in ms."
    )
    was_truncated: bool = Field(
        default=False, description="True if evidence was truncated to fit prompt budget."
    )
    omitted_evidence_categories: list[str] = Field(
        default_factory=list, description="Categories dropped during budget pruning."
    )

    @model_validator(mode="after")
    def validate_budget_partition(self) -> InferenceMetadata:
        total = (
            self.prompt_budget_tokens
            + self.output_budget_tokens
            + self.application_safety_margin_tokens
        )
        if total != self.context_window_tokens:
            raise ValueError(
                f"Budget partition must sum to context window: {total} != {self.context_window_tokens}"
            )
        return self


# =============================================================================
# Top-Level Versioned JSON Envelope
# =============================================================================


class JsonEnvelope(BaseModel, Generic[T]):
    """Standard machine-readable JSON output envelope preserving raw data verbatim."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Fixed schema version string ('1.0')."
    )
    command: str = Field(description="Executing CLI command name.")
    success: bool = Field(description="True if command completed successfully.")
    target_path: str | None = Field(
        default=None, description="Target source path associated with this command."
    )
    data: T | None = Field(default=None, description="Command-specific structured payload.")
    errors: list[str] = Field(
        default_factory=list, description="Fatal or actionable error messages."
    )
    limitations: list[str] = Field(
        default_factory=list, description="Explicit technical limitations or abstentions."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Command and environment metadata."
    )
