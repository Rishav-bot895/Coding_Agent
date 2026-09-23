"""Unit tests for terminal control-sequence sanitization and encoding fallback (P2-T4).

Verifies stripping of hostile ANSI CSI sequences, OSC window title and hyperlink
commands, DCS sequences, and ASCII control characters, while preserving standard
newlines, tabs, and unicode content. Verifies graceful ASCII fallback for consoles.
"""

from __future__ import annotations

import pytest

from localdev.reporting.sanitizer import safe_terminal_encode, sanitize_terminal_text


# =============================================================================
# Hostile Sequence Sanitization
# =============================================================================


def test_sanitize_csi_screen_clear_and_cursor() -> None:
    """CSI sequences for screen clear and cursor positioning must be stripped."""
    hostile = "Before\x1b[2J\x1b[H\x1b[1;1HAfter"
    assert sanitize_terminal_text(hostile) == "BeforeAfter"


def test_sanitize_csi_colors_and_text_styles() -> None:
    """CSI color, bold, underline, and reset sequences must be stripped."""
    styled = "\x1b[31;1mRed Bold\x1b[0m \x1b[4mUnderlined\x1b[24m"
    assert sanitize_terminal_text(styled) == "Red Bold Underlined"


def test_sanitize_osc_title_and_commands() -> None:
    """OSC terminal commands terminated by BEL or ST must be stripped."""
    # OSC title set terminated by BEL (\x07)
    osc_bel = "\x1b]0;Malicious Window Title\x07Text"
    assert sanitize_terminal_text(osc_bel) == "Text"

    # OSC title set terminated by String Terminator ST (\x1b\\)
    osc_st = "\x1b]0;Malicious Window Title\x1b\\Text"
    assert sanitize_terminal_text(osc_st) == "Text"

    # OSC 8 Hyperlink
    osc_link = "\x1b]8;;http://evil.example.com\x1b\\Click Here\x1b]8;;\x1b\\"
    assert sanitize_terminal_text(osc_link) == "Click Here"


def test_sanitize_control_characters() -> None:
    """Dangerous ASCII control characters must be stripped."""
    # Null (\x00), Bell (\x07), Backspace (\x08), Form Feed (\x0c), DEL (\x7f)
    bad_chars = "A\x00B\x01C\x07D\x08E\x0bF\x0cG\x1eH\x1fI\x7fJ"
    assert sanitize_terminal_text(bad_chars) == "ABCDEFGHIJ"


def test_preserves_standard_formatting() -> None:
    """Horizontal tabs and newlines must be strictly preserved."""
    formatted = "def foo():\n\tline1 = 1\n\treturn line1\n"
    assert sanitize_terminal_text(formatted) == formatted


def test_crlf_normalization() -> None:
    """CRLF newlines are cleanly preserved/normalized as standard linebreaks."""
    crlf_text = "line1\r\nline2\r\n"
    assert sanitize_terminal_text(crlf_text) == "line1\nline2\n"

    # Standalone \r is converted to \n to prevent terminal line overwrite
    standalone_cr = "first\rsecond"
    assert sanitize_terminal_text(standalone_cr) == "first\nsecond"


def test_preserves_unicode_characters() -> None:
    """Unicode text (accents, non-Latin scripts, emoji) must be preserved."""
    unicode_text = "Café au lait — Alpha α, Beta β, Gamma γ 🐍"
    assert sanitize_terminal_text(unicode_text) == unicode_text


def test_empty_string_sanitization() -> None:
    """Empty string returns empty string."""
    assert sanitize_terminal_text("") == ""


# =============================================================================
# Encoding Fallback & Console Compatibility
# =============================================================================


def test_safe_terminal_encode_utf8() -> None:
    """UTF-8 stream encoding preserves all Unicode symbols untouched."""
    text = "✓ Success: alpha α → beta β"
    assert safe_terminal_encode(text, target_encoding="utf-8") == text


def test_safe_terminal_encode_ascii_fallback() -> None:
    """ASCII target encoding maps common symbols to readable ASCII approximations."""
    text = "✓ OK ✗ FAIL → NEXT • ITEM — DASH"
    encoded = safe_terminal_encode(text, target_encoding="ascii")

    assert "[OK]" in encoded
    assert "[FAIL]" in encoded
    assert "->" in encoded
    assert "*" in encoded
    assert "--" in encoded
    # Must be encodable as ascii without error
    encoded.encode("ascii")


def test_safe_terminal_encode_unknown_encoding() -> None:
    """Unknown encoding string falls back gracefully to ASCII replacement."""
    text = "✓ Test"
    encoded = safe_terminal_encode(text, target_encoding="non_existent_codepage_xyz")
    assert "[OK] Test" in encoded

