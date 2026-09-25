"""Structured patch applier and candidate generation.

Applies validated edit proposals (EditProposalRecord) to an in-memory/session candidate
representation without modifying the original source file on disk.

Enforces:
- Reverse line order application (or immutable line reconstruction) to prevent line index shifts.
- Exact-match verification of expected_text against target source lines before applying each chunk.
- Preservation of file metadata: encoding, UTF-8 BOM, newline style (\\r\\n vs \\n), and trailing newline presence.
- Line ending normalization: model supplies \\n; applier normalizes to target newline style.
- Contextual unified diff rendering for user review.
- Preparation for atomic write: safe candidate serialization into dedicated staging directories.
- Zero-mutation guarantee: original target file on disk remains completely untouched.
"""

from __future__ import annotations

import codecs
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from localdev.errors import PatchApplicationError
from localdev.patching.diff_renderer import render_unified_diff
from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.patching.edit_validator import EditValidator

if TYPE_CHECKING:
    from localdev.schemas import TargetRecord


class PatchCandidate:
    """In-memory candidate representation of a patched target file ready for review and staging.

    Attributes:
        target_name: Basename of the target file.
        target_path: Canonical or resolved Path of the target file.
        source_text: Decoded pre-patch source text.
        patched_text: Decoded post-patch reconstructed text.
        patched_bytes: Raw bytes of the patched file with exact encoding, BOM, and line endings.
        encoding: Character encoding (e.g. 'utf-8', 'latin-1').
        has_bom: Whether the file begins with a UTF-8 BOM.
        newline_style: Line ending convention ('\\r\\n', '\\n', or 'mixed').
        has_trailing_newline: Whether the patched file ends with a trailing newline.
        diff: Standard 3-line contextual unified diff string.
        proposal: The validated EditProposalRecord applied.
        sha256: SHA-256 hex digest of the patched bytes.
        baseline_sha256: SHA-256 hex digest of the pre-patch file bytes.
    """

    def __init__(
        self,
        target_name: str,
        target_path: Path,
        source_text: str,
        patched_text: str,
        patched_bytes: bytes,
        encoding: str,
        has_bom: bool,
        newline_style: Literal["\r\n", "\n", "mixed"],
        has_trailing_newline: bool,
        diff: str,
        proposal: EditProposalRecord,
        sha256: str,
        baseline_sha256: str,
    ) -> None:
        self.target_name = target_name
        self.target_path = target_path
        self.source_text = source_text
        self.patched_text = patched_text
        self.patched_bytes = patched_bytes
        self.encoding = encoding
        self.has_bom = has_bom
        self.newline_style = newline_style
        self.has_trailing_newline = has_trailing_newline
        self.diff = diff
        self.proposal = proposal
        self.sha256 = sha256
        self.baseline_sha256 = baseline_sha256

    @property
    def line_count(self) -> int:
        """Total line count of the patched text."""
        return len(self.patched_text.splitlines())

    @property
    def is_noop(self) -> bool:
        """True if the patch produces zero textual changes."""
        return self.source_text == self.patched_text

    @property
    def changed_line_count(self) -> int:
        """Total changed lines across all edit operations in the proposal."""
        total = 0
        for edit in self.proposal.edits:
            exp_cnt = len(edit.expected_text.splitlines()) if edit.expected_text else 0
            rep_cnt = len(edit.replacement_text.splitlines()) if edit.replacement_text else 0
            total += max(exp_cnt, rep_cnt)
        return total

    def write_to_staging(
        self,
        staging_dir: Path | str,
        filename: str | None = None,
    ) -> Path:
        """Write candidate bytes to a dedicated staging directory on the same filesystem volume.

        Guarantees that the original target file on disk is never touched or overwritten.

        Args:
            staging_dir: Staging directory path where candidate file will be written.
            filename: Optional candidate file name; defaults to 'candidate_{target_name}'.

        Returns:
            Resolved absolute Path of the staged candidate file.

        Raises:
            PatchApplicationError: If destination path collides with the original target file.
        """
        stg = Path(staging_dir)
        stg.mkdir(parents=True, exist_ok=True)
        out_name = filename if filename is not None else f"candidate_{self.target_name}"
        staged_path = (stg / out_name).resolve()

        if staged_path == self.target_path.resolve():
            raise PatchApplicationError(
                f"Candidate staging destination cannot overwrite the original target file: {staged_path}"
            )

        staged_path.write_bytes(self.patched_bytes)
        return staged_path

    def write_to_path(
        self,
        dest_path: Path | str,
        allow_target_overwrite: bool = False,
    ) -> Path:
        """Write candidate bytes to an explicit destination path.

        Args:
            dest_path: Destination file path.
            allow_target_overwrite: Must be explicitly True if writing to target_path.

        Returns:
            Resolved absolute Path of the written file.

        Raises:
            PatchApplicationError: If writing to the target path without permission.
        """
        p = Path(dest_path).resolve()
        if not allow_target_overwrite and p == self.target_path.resolve():
            raise PatchApplicationError(
                f"Direct overwrite of original target file forbidden in candidate mode: {p}"
            )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.patched_bytes)
        return p

    def __repr__(self) -> str:
        return (
            f"PatchCandidate(target='{self.target_name}', lines={self.line_count}, "
            f"changed={self.changed_line_count}, sha256='{self.sha256[:8]}...')"
        )


class PatchApplier:
    """Applies validated EditProposalRecord proposals to create in-memory PatchCandidate instances.

    Enforces:
    - Target file verification and exact expected_text matching.
    - Reverse line order application to eliminate line coordinate shift hazards.
    - Full metadata preservation (encoding, BOM, newline style, trailing newline).
    - Absolute guarantee that original source target files on disk are never modified.
    """

    def __init__(self, context_lines: int = 3) -> None:
        self.context_lines = context_lines

    def apply_to_lines(
        self,
        lines: list[str],
        edits: list[EditOperation],
    ) -> list[str]:
        """Apply a sequence of edit operations to logical source lines in reverse line order.

        Verifies exact expected_text match for each edit chunk before application.
        Any mismatch aborts application immediately by raising PatchApplicationError.

        Args:
            lines: List of logical source lines (without trailing newlines).
            edits: List of validated EditOperation items.

        Returns:
            New list of logical lines reflecting applied edits.

        Raises:
            PatchApplicationError: If expected_text fails to match the target lines.
        """
        new_lines = list(lines)
        total_lines = len(lines)

        # Sort edits defensively by start_line ascending
        sorted_edits = sorted(edits, key=lambda e: (e.start_line, e.end_line))

        # 1. Exact-match expected_text verification before any slice mutation
        for idx, edit in enumerate(sorted_edits):
            if edit.operation in (EditOperationType.REPLACE, EditOperationType.DELETE):
                span = edit.end_line - edit.start_line + 1
                expected_lines = edit.expected_text.splitlines() if edit.expected_text else []
                if len(expected_lines) != span:
                    raise PatchApplicationError(
                        f"Edit {idx} ({edit.operation.value}): expected_text has {len(expected_lines)} lines, "
                        f"but range [{edit.start_line}, {edit.end_line}] spans {span} lines.",
                        edit_index=idx,
                    )
                target_slice = lines[edit.start_line - 1 : edit.end_line]
                for offset, (actual_line, exp_line) in enumerate(
                    zip(target_slice, expected_lines, strict=True)
                ):
                    if actual_line != exp_line:
                        line_num = edit.start_line + offset
                        raise PatchApplicationError(
                            f"Edit {idx} ({edit.operation.value}): expected_text mismatch at line {line_num}.\n"
                            f"  Expected: {exp_line!r}\n"
                            f"  Actual:   {actual_line!r}",
                            edit_index=idx,
                        )
            elif edit.operation == EditOperationType.INSERT:
                if edit.expected_text != "":
                    raise PatchApplicationError(
                        f"Edit {idx} (insert): expected_text must be empty, got {edit.expected_text!r}",
                        edit_index=idx,
                    )

        # 2. Apply edits in reverse line order to prevent line coordinate shift
        for _idx, edit in reversed(list(enumerate(sorted_edits))):
            if edit.operation == EditOperationType.DELETE:
                replacement_lines: list[str] = []
            else:
                replacement_lines = (
                    edit.replacement_text.splitlines() if edit.replacement_text else []
                )

            if total_lines == 0:
                # Empty file insertion
                new_lines = replacement_lines
            else:
                start_idx = edit.start_line - 1
                end_idx = edit.end_line
                new_lines[start_idx:end_idx] = replacement_lines

        return new_lines

    def apply(
        self,
        proposal: EditProposalRecord,
        target: TargetRecord | Path | str,
        source_text: str | None = None,
        raw_bytes: bytes | None = None,
    ) -> PatchCandidate:
        """Validate and apply an edit proposal to generate an in-memory PatchCandidate.

        Guarantees that the original source file on disk is never modified.

        Args:
            proposal: The EditProposalRecord containing candidate line edits.
            target: TargetRecord, or Path/str to the target file.
            source_text: Optional pre-decoded source text. If None, read from raw_bytes or disk.
            raw_bytes: Optional raw byte buffer of the target file.

        Returns:
            PatchCandidate instance holding reconstructed text, raw bytes, metadata, and diff.

        Raises:
            PatchApplicationError: If validation or expected_text matching fails.
        """
        from localdev.schemas import TargetRecord

        newline_style: Literal["\r\n", "\n", "mixed"]

        if isinstance(target, TargetRecord):
            target_name = Path(target.path).name
            target_path = Path(target.absolute_path)
            encoding = target.encoding
            has_bom = target.has_bom
            newline_style = target.newline_style
            has_trailing_newline = target.has_trailing_newline
            baseline_sha256 = target.sha256

            if source_text is None:
                if raw_bytes is None:
                    if not target_path.is_file():
                        raise PatchApplicationError(
                            f"Target file does not exist on disk: {target_path}"
                        )
                    raw_bytes = target_path.read_bytes()

                clean_bytes = (
                    raw_bytes[len(codecs.BOM_UTF8) :]
                    if has_bom and raw_bytes.startswith(codecs.BOM_UTF8)
                    else raw_bytes
                )
                source_text = clean_bytes.decode(encoding)
        else:
            from localdev.agent.permissions import (
                detect_encoding_and_bom,
                detect_newline_style,
            )

            p = Path(target)
            target_name = p.name
            target_path = p.resolve()

            if raw_bytes is None and p.is_file():
                raw_bytes = p.read_bytes()

            if raw_bytes is not None:
                encoding, has_bom = detect_encoding_and_bom(raw_bytes)
                newline_style, has_trailing_newline = detect_newline_style(raw_bytes)
                baseline_sha256 = hashlib.sha256(raw_bytes).hexdigest()
                if source_text is None:
                    clean_bytes = (
                        raw_bytes[len(codecs.BOM_UTF8) :]
                        if has_bom and raw_bytes.startswith(codecs.BOM_UTF8)
                        else raw_bytes
                    )
                    source_text = clean_bytes.decode(encoding)
            else:
                if source_text is None:
                    raise PatchApplicationError(
                        f"Source text must be provided for non-existent target: {target}"
                    )
                encoding = "utf-8"
                has_bom = False
                newline_style = "\r\n" if "\r\n" in source_text else "\n"
                has_trailing_newline = source_text.endswith(("\n", "\r"))
                baseline_sha256 = hashlib.sha256(source_text.encode(encoding)).hexdigest()

        # Step 1: Validate proposal against target identity, bounds, and expected_text
        validator = EditValidator(target=target, source_text=source_text)
        validator.validate(proposal)

        # Step 2: Split logical lines and apply edits in reverse order
        logical_lines = source_text.splitlines()
        new_lines = self.apply_to_lines(logical_lines, proposal.edits)

        # Step 3: Normalize newlines and preserve trailing newline
        line_sep = "\r\n" if newline_style == "\r\n" else "\n"
        orig_line_count = len(logical_lines)

        # Trailing newline decision:
        # If edit does not touch final line, preserve original trailing newline state.
        # If edit does touch final line (end_line == N or start_line == N + 1), check if edit requested newline.
        touches_final_line = orig_line_count == 0 or any(
            e.end_line == orig_line_count or e.start_line == orig_line_count + 1
            for e in proposal.edits
        )

        if not touches_final_line:
            candidate_trailing_newline = has_trailing_newline
        else:
            if has_trailing_newline:
                candidate_trailing_newline = True
            else:
                last_edit = max(proposal.edits, key=lambda e: e.start_line)
                candidate_trailing_newline = last_edit.replacement_text.endswith(("\n", "\r"))

        if not new_lines:
            patched_text = ""
        else:
            patched_text = line_sep.join(new_lines)
            if candidate_trailing_newline:
                patched_text += line_sep

        # Step 4: Encode to bytes preserving encoding and UTF-8 BOM
        encoded_bytes = patched_text.encode(encoding)
        if has_bom and not encoded_bytes.startswith(codecs.BOM_UTF8):
            patched_bytes = codecs.BOM_UTF8 + encoded_bytes
        else:
            patched_bytes = encoded_bytes

        # Step 5: Render contextual unified diff
        diff = render_unified_diff(
            source_text=source_text,
            patched_text=patched_text,
            target_name=target_name,
            context_lines=self.context_lines,
        )

        patched_sha256 = hashlib.sha256(patched_bytes).hexdigest()

        return PatchCandidate(
            target_name=target_name,
            target_path=target_path,
            source_text=source_text,
            patched_text=patched_text,
            patched_bytes=patched_bytes,
            encoding=encoding,
            has_bom=has_bom,
            newline_style=newline_style,
            has_trailing_newline=candidate_trailing_newline,
            diff=diff,
            proposal=proposal,
            sha256=patched_sha256,
            baseline_sha256=baseline_sha256,
        )
