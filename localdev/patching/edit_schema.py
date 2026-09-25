"""Unambiguous structured patch and edit schema with frozen indexing rules.

Enforces:
- Frozen indexing semantics:
  - Line numbers are 1-based and inclusive (start_line, end_line).
  - Lines are indexed by logical line content.
  - Replacement: start_line <= end_line; expected_text must match lines [start_line, end_line] exactly;
    replacement_text contains new lines.
  - Insertion before line K: start_line = K, end_line = K - 1; expected_text = "";
    replacement_text contains lines to insert.
  - EOF insertion (append): for file with N lines, start_line = N + 1, end_line = N; expected_text = "".
  - Deletion: start_line <= end_line; expected_text matches lines to delete; replacement_text = "".
  - Empty file (0 lines): insertion at start_line = 1, end_line = 0.
- Ordering and overlap: multiple edits must be strictly ordered by increasing line numbers
  (start_line_i > start_line_{i-1} and start_line_i > end_line_{i-1}); overlapping or contiguous ambiguous
  edits are rejected.
- Edit bounds: maximum 8 edits and maximum 80 total changed lines per proposal.
- Path safety: target_file must not contain path traversal (..) or shell metacharacters.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from localdev.constants import MAX_PATCH_CHANGED_LINES, MAX_PATCH_EDITS


class EditOperationType(str, Enum):
    """Supported single-file edit operation types."""

    REPLACE = "replace"
    INSERT = "insert"
    DELETE = "delete"


class EditOperation(BaseModel):
    """Single line-oriented edit operation within a single source file.

    Follows frozen indexing semantics:
    - 1-based, inclusive line numbers (start_line, end_line).
    - Replacement: start_line <= end_line; expected_text matches target lines; replacement_text contains new lines.
    - Insertion before line K: start_line = K, end_line = K - 1; expected_text = ""; replacement_text contains lines to insert.
    - EOF insertion: for file with N lines, start_line = N + 1, end_line = N; expected_text = "".
    - Deletion: start_line <= end_line; expected_text matches target lines; replacement_text = "".
    - Empty file (0 lines): insertion at start_line = 1, end_line = 0.
    """

    model_config = ConfigDict(extra="forbid")

    operation: EditOperationType = Field(
        description="Operation type: replace, insert, or delete."
    )
    start_line: int = Field(ge=1, description="1-based inclusive starting line.")
    end_line: int = Field(
        ge=0,
        description="1-based inclusive ending line (0 for insert before line 1 or in empty file).",
    )
    expected_text: str = Field(
        default="",
        description="Normalized text expected in target file at specified line range. Empty for insert.",
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

        if self.operation == EditOperationType.INSERT:
            if self.start_line != self.end_line + 1:
                raise ValueError(
                    f"insert operation requires start_line == end_line + 1, "
                    f"got start_line={self.start_line} and end_line={self.end_line}"
                )
            if self.expected_text != "":
                raise ValueError(
                    f"insert operation requires expected_text to be empty, got {self.expected_text!r}"
                )

        if self.operation == EditOperationType.DELETE and self.replacement_text != "":
            raise ValueError(
                f"delete operation requires replacement_text to be empty, got {self.replacement_text!r}"
            )

        return self


class EditProposalRecord(BaseModel):
    """Structured patch proposal containing validated line edits for one target file.

    Enforces 1-based inclusive line indexing, bounded edit counts, ordering, and path safety.
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

    @model_validator(mode="after")
    def validate_edits_ordering_and_overlap(self) -> EditProposalRecord:
        for i in range(1, len(self.edits)):
            prev = self.edits[i - 1]
            curr = self.edits[i]
            if curr.start_line <= prev.start_line or curr.start_line <= prev.end_line:
                raise ValueError(
                    f"Edits must be strictly ordered by increasing line numbers without overlap: "
                    f"edit {i} starts at line {curr.start_line}, which is <= previous edit ending at line {prev.end_line} "
                    f"(or starting at line {prev.start_line})"
                )
        return self
