"""Deterministic Python traceback parser and error signature extractor.

In accordance with the Runtime and Isolation Contract:
- Parses standard and chained ('raise ... from ...') Python execution tracebacks.
- Normalizes relocated temporary session copy paths back to canonical user target paths.
- Classifies frames as target-local (is_target=True) versus external (is_target=False).
- Extracts normalized error signatures (exception_type, normalized_message, top_target_file, top_target_line).
- Never opens, reads, or inspects external source files.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from localdev.patching.edit_schema import EditOperation

from localdev.reporting.sanitizer import strip_ansi
from localdev.schemas import ErrorSignature, TracebackFrame

FRAME_PATTERN = re.compile(r'^\s*File "([^"]+)", line (\d+)(?:, in (.+))?')
REPEAT_PATTERN = re.compile(r"^\s*\[Previous line repeated \d+ more times\]")
CARET_PATTERN = re.compile(r"^\s*[\^~ ]+$")

# Pattern for Python exception lines, e.g. "ValueError: invalid literal" or "ZeroDivisionError"
EXCEPTION_PATTERN = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_.]*(?:Error|Exception|Warning|Interrupt|Exit|Iteration)?)(?::\s*(.*))?$"
)


def normalize_frame_path(
    raw_path: str,
    target_path: str | Path,
    session_target_path: str | Path | None = None,
) -> tuple[str, bool]:
    """Normalize a traceback frame file path and determine if it belongs to the target.

    Args:
        raw_path: File path as emitted by Python in the traceback.
        target_path: Canonical user-facing target file path.
        session_target_path: Optional relocated temporary session copy path.

    Returns:
        Tuple of (normalized_file_path, is_target_boolean).
    """
    target_str = str(target_path)
    try:
        norm_raw = os.path.normcase(os.path.abspath(raw_path))
    except (OSError, ValueError):
        norm_raw = os.path.normcase(raw_path)

    # Check if frame matches relocated temporary session copy
    if session_target_path is not None:
        try:
            norm_session = os.path.normcase(os.path.abspath(str(session_target_path)))
            if norm_raw == norm_session:
                return target_str, True
        except (OSError, ValueError):
            norm_session = None

    # Check if frame matches canonical target path
    try:
        norm_target = os.path.normcase(os.path.abspath(target_str))
        if norm_raw == norm_target:
            return target_str, True
    except (OSError, ValueError):
        norm_target = None

    # Match by simple filename if raw_path is relative or session filename
    if session_target_path is not None and Path(raw_path).name == Path(session_target_path).name:
        return target_str, True

    if Path(raw_path).name == Path(target_str).name:
        return target_str, True

    # External frame: preserve reported path without opening external files
    return raw_path, False


def parse_traceback(
    stderr_text: str,
    target_path: str | Path,
    session_target_path: str | Path | None = None,
) -> tuple[list[TracebackFrame], ErrorSignature | None]:
    """Parse execution stderr into structured TracebackFrames and an ErrorSignature.

    Args:
        stderr_text: Captured stderr output from the target execution.
        target_path: Canonical user-facing target file path.
        session_target_path: Optional relocated temporary session copy path.

    Returns:
        Tuple of (frames_list, error_signature_or_none).
    """
    if not stderr_text.strip():
        return [], None

    clean_text = strip_ansi(stderr_text)
    lines = clean_text.splitlines()

    frames: list[TracebackFrame] = []
    current_frame_header: tuple[str, int, str] | None = None
    current_code_lines: list[str] = []

    last_exc_type: str | None = None
    last_exc_msg: str | None = None

    def flush_frame() -> None:
        nonlocal current_frame_header, current_code_lines
        if current_frame_header is None:
            return
        raw_fpath, line_no, func_name = current_frame_header
        norm_fpath, is_target = normalize_frame_path(
            raw_fpath,
            target_path=target_path,
            session_target_path=session_target_path,
        )
        code_str = "\n".join(current_code_lines).strip()
        frames.append(
            TracebackFrame(
                file_path=norm_fpath,
                line_number=line_no,
                function_name=func_name,
                code_line=code_str,
                is_target=is_target,
            )
        )
        current_frame_header = None
        current_code_lines = []

    idx = 0
    total_lines = len(lines)
    while idx < total_lines:
        line = lines[idx]

        # 1. Match Frame Header:   File "...", line X, in Y
        frame_match = FRAME_PATTERN.match(line)
        if frame_match:
            flush_frame()
            raw_file = frame_match.group(1)
            line_num = max(1, int(frame_match.group(2)))
            func_name = frame_match.group(3) or "<module>"
            current_frame_header = (raw_file, line_num, func_name)
            idx += 1
            continue

        # 2. Skip Caret / Repeat indicator lines
        if REPEAT_PATTERN.match(line) or CARET_PATTERN.match(line):
            idx += 1
            continue

        # 3. Match Exception line at root (not indented)
        if not line.startswith((" ", "\t")):
            exc_match = EXCEPTION_PATTERN.match(line)
            if exc_match:
                flush_frame()
                last_exc_type = exc_match.group(1).strip()
                raw_msg = exc_match.group(2) or ""
                # Accumulate any multiline exception message
                msg_lines = [raw_msg]
                lookahead = idx + 1
                while lookahead < total_lines and lines[lookahead].startswith((" ", "\t")):
                    msg_lines.append(lines[lookahead].strip())
                    lookahead += 1
                last_exc_msg = " ".join(part for part in msg_lines if part).strip()
                idx = lookahead
                continue

        # 4. If inside a frame header, collect source code lines
        if current_frame_header is not None and line.startswith((" ", "\t")):
            current_code_lines.append(line.strip())

        idx += 1

    flush_frame()

    if last_exc_type is None:
        # Fallback: Check if there's any mention of common exceptions in raw text
        for line in reversed(lines):
            line_str = line.strip()
            match = EXCEPTION_PATTERN.match(line_str)
            if match:
                last_exc_type = match.group(1).strip()
                last_exc_msg = (match.group(2) or "").strip()
                break

    if last_exc_type is None:
        return frames, None

    # Determine highest / innermost target frame for error signature
    target_frames = [f for f in frames if f.is_target]
    top_target_file: str | None = None
    top_target_line: int | None = None

    if target_frames:
        top_frame = target_frames[-1]
        top_target_file = top_frame.file_path
        top_target_line = top_frame.line_number
    elif frames:
        # If no target frames, record the failing external frame's info
        top_target_file = frames[-1].file_path
        top_target_line = frames[-1].line_number
    else:
        top_target_file = str(target_path)
        top_target_line = None

    signature = ErrorSignature(
        exception_type=last_exc_type,
        normalized_message=last_exc_msg or "",
        top_target_file=top_target_file,
        top_target_line=top_target_line,
    )

    return frames, signature


def is_same_error_signature(
    sig_a: ErrorSignature | None,
    sig_b: ErrorSignature | None,
    *,
    edits: Sequence[EditOperation] | None = None,
) -> bool:
    """Determine if candidate error signature reproduces the baseline error signature.

    Compares:
    - Exception type (e.g. ZeroDivisionError == ZeroDivisionError)
    - Normalized exception message (e.g. division by zero)
    - Top target file (by filename)

    Args:
        sig_a: ErrorSignature from pre-patch baseline run.
        sig_b: ErrorSignature from post-patch candidate run.
        edits: Optional applied edits sequence to map shifted line numbers.

    Returns:
        True if candidate error signature reproduces the baseline failure, False otherwise.
    """
    if sig_a is None and sig_b is None:
        return True
    if sig_a is None or sig_b is None:
        return False

    if sig_a.exception_type != sig_b.exception_type:
        return False

    if sig_a.normalized_message != sig_b.normalized_message:
        return False

    # Check target file basename if both are present
    return not (
        sig_a.top_target_file
        and sig_b.top_target_file
        and Path(sig_a.top_target_file).name != Path(sig_b.top_target_file).name
    )

