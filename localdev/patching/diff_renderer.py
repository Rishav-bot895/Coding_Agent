"""Standard contextual unified diff rendering for structured patch review.

Provides:
- render_unified_diff: Generates standard 3-line contextual unified diffs
  with '--- a/...' and '+++ b/...' headers.
- colorize_unified_diff: Optional ANSI colorization for terminal diff display.
"""

from __future__ import annotations

import difflib
from pathlib import Path


def render_unified_diff(
    source_text: str,
    patched_text: str,
    target_name: str = "file.py",
    context_lines: int = 3,
    fromfile: str | None = None,
    tofile: str | None = None,
) -> str:
    """Render a standard 3-line contextual unified diff between source and patched text.

    Args:
        source_text: Pre-patch source text.
        patched_text: Post-patch reconstructed text.
        target_name: Target file name or path used in diff headers.
        context_lines: Number of context lines around change hunks (default: 3).
        fromfile: Optional custom 'fromfile' header (defaults to 'a/{target_name}').
        tofile: Optional custom 'tofile' header (defaults to 'b/{target_name}').

    Returns:
        Unified diff string with trailing newline, or empty string if identical.
    """
    if source_text == patched_text:
        return ""

    # Normalize target path separators to POSIX for clean diff headers
    clean_target = str(Path(target_name).as_posix()) if target_name else "file.py"

    header_from = fromfile if fromfile is not None else f"a/{clean_target}"
    header_to = tofile if tofile is not None else f"b/{clean_target}"

    source_lines = source_text.splitlines()
    patched_lines = patched_text.splitlines()

    diff_iter = difflib.unified_diff(
        source_lines,
        patched_lines,
        fromfile=header_from,
        tofile=header_to,
        n=context_lines,
        lineterm="",
    )
    diff_lines = list(diff_iter)
    if not diff_lines:
        return ""

    return "\n".join(diff_lines) + "\n"


def colorize_unified_diff(diff_text: str) -> str:
    """Colorize a unified diff string with ANSI escape codes for terminal display.

    ANSI colors:
    - '---' / '+++' headers: Bold
    - '@@' hunk headers: Cyan (\x1b[36m)
    - '-' deletion lines: Red (\x1b[31m)
    - '+' addition lines: Green (\x1b[32m)

    Args:
        diff_text: Unified diff string.

    Returns:
        ANSI-colorized diff string.
    """
    if not diff_text:
        return ""

    ansi_reset = "\x1b[0m"
    ansi_bold = "\x1b[1m"
    ansi_red = "\x1b[31m"
    ansi_green = "\x1b[32m"
    ansi_cyan = "\x1b[36m"

    colorized: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("---", "+++")):
            colorized.append(f"{ansi_bold}{line}{ansi_reset}")
        elif line.startswith("@@"):
            colorized.append(f"{ansi_cyan}{line}{ansi_reset}")
        elif line.startswith("-"):
            colorized.append(f"{ansi_red}{line}{ansi_reset}")
        elif line.startswith("+"):
            colorized.append(f"{ansi_green}{line}{ansi_reset}")
        else:
            colorized.append(line)

    return "\n".join(colorized) + ("\n" if diff_text.endswith("\n") else "")
