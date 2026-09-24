"""Terminal control-sequence sanitization for localdev.

Sanitizes all untrusted strings (source code, execution stdout/stderr, tracebacks,
and model-generated text) before rendering to the terminal. Strips ANSI escape
sequences (CSI, OSC, DCS), virtual terminal cursor and screen commands, and
dangerous ASCII control characters, while strictly preserving formatting newlines
and horizontal tabs.
"""

from __future__ import annotations

import re
from typing import Final

# ANSI CSI (Control Sequence Introducer): ESC [ ... final_byte
# Covers colors, cursor movement, screen clearing, device status requests, etc.
_ANSI_CSI_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
)

# ANSI OSC (Operating System Command): ESC ] ... BEL (\x07) or ST (ESC \)
# Covers terminal window title changes, hyperlink definitions, clipboard access, etc.
_ANSI_OSC_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
)

# Other multi-byte and 2-byte escape sequences:
# ESC P ... ESC \ (DCS), ESC ^ ... ESC \ (PM), ESC _ ... ESC \ (APC), ESC \ (ST)
# Single 2-byte sequences: ESC [@-Z\\-_]
_ANSI_OTHER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\x1b[PX^_][^\x1b]*\x1b\\|\x1b[@-Z\\-_]"
)

# Dangerous ASCII control characters to strip:
# \x00-\x08: Null, SOH, STX, ETX, EOT, ENQ, ACK, BEL, Backspace
# \x0b: Vertical Tab
# \x0c: Form Feed
# \x0e-\x1f: SO, SI, DLE, DC1-4, NAK, SYN, ETB, CAN, EM, SUB, ESC, FS, GS, RS, US
# \x7f: DEL (rubout)
#
# Preserved formatting characters:
# \x09: Horizontal Tab (\t)
# \x0a: Line Feed (\n)
# \x0d: Carriage Return (\r) - preserved when preceding \n, stripped when standalone
_CONTROL_CHARS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)

# Unicode glyph fallback mapping for legacy Windows console code pages
_GLYPH_ASCII_FALLBACKS: Final[dict[str, str]] = {
    "✓": "[OK]",
    "✔": "[OK]",
    "✗": "[FAIL]",
    "✖": "[FAIL]",
    "→": "->",
    "←": "<-",
    "▲": "^",
    "▼": "v",
    "•": "*",
    "…": "...",
    "—": "--",
    "–": "-",
}


def sanitize_terminal_text(text: str) -> str:
    """Sanitize text for terminal rendering by stripping ANSI and control characters.

    Args:
        text: Input string potentially containing hostile control sequences.

    Returns:
        Cleaned string safe for terminal rendering. Standard tabs and newlines
        are preserved.
    """
    if not text:
        return ""

    # 1. Strip OSC sequences (e.g. setting window title, OSC 8 hyperlinks)
    cleaned = _ANSI_OSC_PATTERN.sub("", text)

    # 2. Strip CSI sequences (e.g. colors, cursor movement, screen clear)
    cleaned = _ANSI_CSI_PATTERN.sub("", cleaned)

    # 3. Strip other escape sequences (DCS, PM, 2-character escapes)
    cleaned = _ANSI_OTHER_PATTERN.sub("", cleaned)

    # 4. Strip dangerous ASCII control characters
    cleaned = _CONTROL_CHARS_PATTERN.sub("", cleaned)

    # 5. Handle carriage returns: normalize standalone \r to avoid line overwriting
    # First preserve legitimate CRLF by temporarily replacing it
    cleaned = cleaned.replace("\r\n", "\n")
    # Any remaining standalone \r is replaced with \n
    cleaned = cleaned.replace("\r", "\n")

    return cleaned


def safe_terminal_encode(text: str, target_encoding: str = "utf-8") -> str:
    """Encode text safely for target console encoding with graceful ASCII fallback.

    If target_encoding cannot represent certain Unicode glyphs (e.g., CP437, CP1252),
    common symbols like checkmarks and arrows are replaced with ASCII approximations.

    Args:
        text: Text to encode.
        target_encoding: Code page or encoding name (e.g., 'cp437', 'utf-8').

    Returns:
        String that can be encoded in target_encoding without UnicodeEncodeError.
    """
    try:
        text.encode(target_encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        # Apply standard glyph fallback mapping
        substituted = text
        for glyph, fallback in _GLYPH_ASCII_FALLBACKS.items():
            if glyph in substituted:
                substituted = substituted.replace(glyph, fallback)

        # Encode with replacement for any remaining unrepresentable characters
        try:
            return substituted.encode(target_encoding, errors="replace").decode(target_encoding)
        except LookupError:
            return substituted.encode("ascii", errors="replace").decode("ascii")


strip_ansi = sanitize_terminal_text


