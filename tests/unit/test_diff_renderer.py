"""Unit tests for contextual unified diff renderer.

Tests:
- Empty diff for identical source and patched texts.
- Standard 3-line contextual unified diff formatting.
- Custom context line counts (n=1, n=5).
- Single-line and multi-line additions, deletions, and replacements.
- Custom headers and Windows path normalization in headers.
- ANSI colorization of headers, hunks, additions, and deletions.
"""

from __future__ import annotations

from localdev.patching.diff_renderer import colorize_unified_diff, render_unified_diff


class TestRenderUnifiedDiff:
    """Test standard contextual unified diff generation."""

    def test_identical_text_returns_empty_diff(self) -> None:
        """Identical texts produce empty diff string."""
        source = "def foo():\n    return 42\n"
        diff = render_unified_diff(source, source, target_name="app.py")
        assert diff == ""

    def test_empty_strings_produce_empty_diff(self) -> None:
        """Both empty strings produce empty diff string."""
        assert render_unified_diff("", "", target_name="empty.py") == ""

    def test_single_line_replacement(self) -> None:
        """Replacing a single line generates valid unified diff with 3 lines of context."""
        source = "line1\nline2\nline3\nline4\nline5\n"
        patched = "line1\nline2\nline3_modified\nline4\nline5\n"

        diff = render_unified_diff(source, patched, target_name="app.py", context_lines=3)

        assert diff.startswith("--- a/app.py\n+++ b/app.py\n")
        assert "@@ -1,5 +1,5 @@" in diff
        assert " line1\n" in diff
        assert " line2\n" in diff
        assert "-line3\n" in diff
        assert "+line3_modified\n" in diff
        assert " line4\n" in diff
        assert " line5\n" in diff

    def test_addition_diff(self) -> None:
        """Adding a line produces + markers without - markers in hunk."""
        source = "line1\nline2\n"
        patched = "line1\nline_inserted\nline2\n"

        diff = render_unified_diff(source, patched, target_name="app.py")
        assert "--- a/app.py\n+++ b/app.py\n" in diff
        assert "+line_inserted\n" in diff
        assert "-line" not in diff

    def test_deletion_diff(self) -> None:
        """Deleting a line produces - markers without + markers in hunk."""
        source = "line1\nline_to_delete\nline2\n"
        patched = "line1\nline2\n"

        diff = render_unified_diff(source, patched, target_name="app.py")
        assert "--- a/app.py\n+++ b/app.py\n" in diff
        assert "-line_to_delete\n" in diff
        assert "+line" not in diff

    def test_custom_context_lines(self) -> None:
        """Context line count respects context_lines parameter."""
        source = "\n".join(f"line_{i}" for i in range(1, 21))
        # Modify line 10
        lines = [f"line_{i}" for i in range(1, 21)]
        lines[9] = "line_10_modified"
        patched = "\n".join(lines)

        # Context lines = 1
        diff_1 = render_unified_diff(source, patched, target_name="numbers.py", context_lines=1)
        assert " line_8\n" not in diff_1
        assert " line_9\n" in diff_1
        assert "-line_10\n" in diff_1
        assert "+line_10_modified\n" in diff_1
        assert " line_11\n" in diff_1
        assert " line_12\n" not in diff_1

        # Context lines = 3 (default)
        diff_3 = render_unified_diff(source, patched, target_name="numbers.py", context_lines=3)
        assert " line_7\n" in diff_3
        assert " line_8\n" in diff_3
        assert " line_9\n" in diff_3
        assert " line_13\n" in diff_3

    def test_windows_path_separator_normalized(self) -> None:
        """Target name with Windows backslashes is normalized to POSIX in diff headers."""
        source = "x = 1\n"
        patched = "x = 2\n"
        diff = render_unified_diff(source, patched, target_name="src\\sub\\module.py")

        assert diff.startswith("--- a/src/sub/module.py\n+++ b/src/sub/module.py\n")

    def test_custom_fromfile_and_tofile_headers(self) -> None:
        """Explicit custom fromfile and tofile headers override default a/ b/ paths."""
        source = "a = 1\n"
        patched = "a = 2\n"
        diff = render_unified_diff(
            source,
            patched,
            fromfile="original/file.py",
            tofile="candidate/file.py",
        )
        assert diff.startswith("--- original/file.py\n+++ candidate/file.py\n")


class TestColorizeUnifiedDiff:
    """Test ANSI terminal colorization of unified diffs."""

    def test_empty_diff_returns_empty(self) -> None:
        """Empty diff string returns empty string."""
        assert colorize_unified_diff("") == ""

    def test_colorize_diff_markers(self) -> None:
        """Headers, hunks, additions, and deletions receive expected ANSI sequences."""
        raw_diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " context\n"
            "-old_line\n"
            "+new_line\n"
        )
        colorized = colorize_unified_diff(raw_diff)

        # Bold headers
        assert "\x1b[1m--- a/file.py\x1b[0m" in colorized
        assert "\x1b[1m+++ b/file.py\x1b[0m" in colorized
        # Cyan hunk headers
        assert "\x1b[36m@@ -1,3 +1,3 @@\x1b[0m" in colorized
        # Red deletions
        assert "\x1b[31m-old_line\x1b[0m" in colorized
        # Green additions
        assert "\x1b[32m+new_line\x1b[0m" in colorized
        # Uncolored context line
        assert " context" in colorized
