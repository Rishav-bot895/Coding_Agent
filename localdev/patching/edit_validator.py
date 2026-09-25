"""Validation of structured edit proposals against target files and source content.

Enforces:
- Target check: proposal target path must match the explicit target file canonical path.
- Line bounds verification:
  - 1-based inclusive coordinates within file line bounds.
  - Replacement & Deletion: 1 <= start_line <= end_line <= N (where N is total logical lines).
  - Insertion before line K: 1 <= K <= N + 1, start_line = K, end_line = K - 1.
  - EOF insertion: start_line = N + 1, end_line = N.
  - Empty file (N = 0): only insertion at start_line = 1, end_line = 0 is permitted.
- Exact expected_text matching:
  - Logical lines in expected_text must match target file lines [start_line, end_line] exactly.
  - Any discrepancy in text or indentation aborts validation immediately.
- Ordering and non-overlap:
  - Multiple edits strictly ordered: start_line_i > start_line_{i-1} and start_line_i > end_line_{i-1}.
- Complexity bounds:
  - Maximum 8 edits and 80 total changed lines per proposal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from localdev.constants import MAX_PATCH_CHANGED_LINES, MAX_PATCH_EDITS
from localdev.errors import EditValidationError
from localdev.patching.edit_schema import (
    EditOperationType,
    EditProposalRecord,
)


class EditValidationResult(BaseModel):
    """Result of validating an edit proposal against target source."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(description="True if the edit proposal is valid and matches target source.")
    errors: list[str] = Field(default_factory=list, description="Validation failure messages if any.")
    total_changed_lines: int = Field(default=0, description="Total lines changed across all edits.")
    target_file: str = Field(description="Target file name or path validated against.")


class EditValidator:
    """Validates EditProposalRecord against a TargetRecord or target path and source text."""

    def __init__(
        self,
        target: Any,  # TargetRecord | Path | str
        source_text: str,
        max_edits: int = MAX_PATCH_EDITS,
        max_changed_lines: int = MAX_PATCH_CHANGED_LINES,
    ) -> None:
        self.target = target
        self.source_text = source_text
        self.max_edits = max_edits
        self.max_changed_lines = max_changed_lines

        # Extract path representations from target
        if hasattr(target, "path") and hasattr(target, "absolute_path"):
            self.target_name: str = Path(str(target.path)).name
            self.target_rel_path: str = str(target.path).replace("\\", "/")
            self.target_abs_path: str = str(target.absolute_path).replace("\\", "/")
            self.canonical_path: Path = Path(str(target.absolute_path))
        else:
            p = Path(target)
            self.target_name = p.name
            self.target_rel_path = p.as_posix()
            self.target_abs_path = p.resolve().as_posix()
            self.canonical_path = p.resolve()

    def _matches_target_path(self, proposed_target: str) -> bool:
        """Check if proposed target_file string securely matches the actual target."""
        # Reject traversal attempts
        if ".." in proposed_target or ("/" in proposed_target and "\\" in proposed_target):
            return False

        norm_prop = proposed_target.replace("\\", "/").rstrip("/")
        prop_path = Path(norm_prop)

        # 1. Exact match on relative path or basename
        if norm_prop == self.target_rel_path or norm_prop == self.target_name:
            return True

        if prop_path.name == self.target_name:
            # If a subdirectory path was given, ensure it matches target relative path
            return not ("/" in norm_prop and not norm_prop.endswith(self.target_rel_path))

        # 2. Match on resolved absolute path if absolute path was provided
        try:
            if prop_path.is_absolute() and prop_path.resolve() == self.canonical_path:
                return True
        except (ValueError, OSError):
            return False

        return False

    def validate_proposal(self, proposal: EditProposalRecord) -> EditValidationResult:
        """Validate an edit proposal against target identity, line bounds, and exact expected text."""
        errors: list[str] = []

        # 1. Target identity check
        if not self._matches_target_path(proposal.target_file):
            errors.append(
                f"Target file mismatch: proposal targets '{proposal.target_file}', "
                f"but session target is '{self.target_rel_path}'."
            )

        # 2. Split logical lines of target source text
        lines = self.source_text.splitlines()
        total_lines = len(lines)

        # 3. Edit count check
        if len(proposal.edits) > self.max_edits:
            errors.append(
                f"Edit count ({len(proposal.edits)}) exceeds maximum allowed limit of {self.max_edits}."
            )

        # 4. Line bounds and exact expected text verification per edit
        total_changed_lines = 0

        for idx, edit in enumerate(proposal.edits):
            exp_lines = len(edit.expected_text.splitlines()) if edit.expected_text else 0
            rep_lines = len(edit.replacement_text.splitlines()) if edit.replacement_text else 0
            total_changed_lines += max(exp_lines, rep_lines)

            # Check bounds for empty file (N = 0)
            if total_lines == 0:
                if edit.operation != EditOperationType.INSERT:
                    errors.append(
                        f"Edit {idx} ({edit.operation.value}): cannot {edit.operation.value} on an empty file (0 lines); "
                        "only insertion at start_line=1, end_line=0 is permitted."
                    )
                elif edit.start_line != 1 or edit.end_line != 0:
                    errors.append(
                        f"Edit {idx} (insert): empty file insertion requires start_line=1, end_line=0 "
                        f"(got start_line={edit.start_line}, end_line={edit.end_line})."
                    )
                continue

            # Check bounds for non-empty file (N > 0)
            if edit.operation in (EditOperationType.REPLACE, EditOperationType.DELETE):
                if edit.start_line < 1:
                    errors.append(
                        f"Edit {idx} ({edit.operation.value}): start_line ({edit.start_line}) must be >= 1."
                    )
                if edit.start_line > total_lines:
                    errors.append(
                        f"Edit {idx} ({edit.operation.value}): start_line ({edit.start_line}) "
                        f"exceeds target file line count ({total_lines})."
                    )
                if edit.end_line > total_lines:
                    errors.append(
                        f"Edit {idx} ({edit.operation.value}): end_line ({edit.end_line}) "
                        f"exceeds target file line count ({total_lines})."
                    )
                if edit.start_line > edit.end_line:
                    errors.append(
                        f"Edit {idx} ({edit.operation.value}): start_line ({edit.start_line}) "
                        f"cannot be greater than end_line ({edit.end_line})."
                    )

                # If bounds are within range, verify exact expected_text match
                if 1 <= edit.start_line <= edit.end_line <= total_lines:
                    expected_lines = edit.expected_text.splitlines()
                    span = edit.end_line - edit.start_line + 1

                    if len(expected_lines) != span:
                        errors.append(
                            f"Edit {idx} ({edit.operation.value}): expected_text contains {len(expected_lines)} lines, "
                            f"but specified line range [{edit.start_line}, {edit.end_line}] spans {span} lines."
                        )
                    else:
                        actual_slice = lines[edit.start_line - 1 : edit.end_line]
                        for offset, (actual_line, expected_line) in enumerate(
                            zip(actual_slice, expected_lines, strict=True)
                        ):
                            line_num = edit.start_line + offset
                            if actual_line != expected_line:
                                errors.append(
                                    f"Edit {idx} ({edit.operation.value}): expected_text mismatch at line {line_num}.\n"
                                    f"  Expected: {expected_line!r}\n"
                                    f"  Actual:   {actual_line!r}"
                                )
                                break

                if edit.operation == EditOperationType.DELETE and edit.replacement_text != "":
                    errors.append(
                        f"Edit {idx} (delete): replacement_text must be empty, got {edit.replacement_text!r}."
                    )

            elif edit.operation == EditOperationType.INSERT:
                if edit.start_line < 1:
                    errors.append(
                        f"Edit {idx} (insert): start_line ({edit.start_line}) must be >= 1."
                    )
                elif edit.start_line > total_lines + 1:
                    errors.append(
                        f"Edit {idx} (insert): start_line ({edit.start_line}) exceeds maximum allowed insertion position "
                        f"({total_lines + 1}) for file with {total_lines} lines."
                    )
                elif edit.end_line != edit.start_line - 1:
                    errors.append(
                        f"Edit {idx} (insert): insertion requires end_line == start_line - 1 "
                        f"(got start_line={edit.start_line}, end_line={edit.end_line})."
                    )

                if edit.expected_text != "":
                    errors.append(
                        f"Edit {idx} (insert): expected_text must be empty, got {edit.expected_text!r}."
                    )

        # 5. Check ordering and overlap across edits
        for i in range(1, len(proposal.edits)):
            prev = proposal.edits[i - 1]
            curr = proposal.edits[i]
            if curr.start_line <= prev.start_line or curr.start_line <= prev.end_line:
                errors.append(
                    f"Edits {i-1} and {i} are out of order or overlapping: "
                    f"edit {i-1} range [{prev.start_line}, {prev.end_line}], "
                    f"edit {i} starts at line {curr.start_line}."
                )

        # 6. Check total changed lines limit
        if total_changed_lines > self.max_changed_lines:
            errors.append(
                f"Total changed lines ({total_changed_lines}) exceeds hard limit of {self.max_changed_lines}."
            )

        return EditValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            total_changed_lines=total_changed_lines,
            target_file=self.target_name,
        )

    def validate(self, proposal: EditProposalRecord) -> None:
        """Validate proposal and raise EditValidationError if invalid."""
        result = self.validate_proposal(proposal)
        if not result.is_valid:
            err_summary = "; ".join(result.errors)
            raise EditValidationError(
                f"Edit proposal validation failed for '{proposal.target_file}': {err_summary}",
                validation_errors=result.errors,
            )

    def validate_raw_payload(
        self,
        raw_payload: str,
    ) -> tuple[EditProposalRecord | None, list[str]]:
        """Parse raw JSON string into EditProposalRecord and validate against target source.

        Returns:
            Tuple of (record, errors). If valid, record is returned with empty error list.
        """
        if not raw_payload or not raw_payload.strip():
            return None, ["Model output is empty or whitespace."]

        try:
            parsed = json.loads(raw_payload)
        except (ValueError, json.JSONDecodeError) as exc:
            return None, [f"Malformed JSON syntax: {exc}"]

        if not isinstance(parsed, dict):
            return None, [f"Expected JSON object (dict), got {type(parsed).__name__}."]

        try:
            record = EditProposalRecord.model_validate(parsed)
        except ValidationError as exc:
            errs = [
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']} (got {err.get('input', 'missing')!r})"
                for err in exc.errors()
            ]
            return None, errs

        result = self.validate_proposal(record)
        if not result.is_valid:
            return None, result.errors

        return record, []
