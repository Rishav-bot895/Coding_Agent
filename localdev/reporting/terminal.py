"""Human-readable terminal output reporter for localdev.

Provides structured, sanitized terminal formatting for command results, status
summaries, evidence citations, unified diffs, validation levels (Levels A–D),
complexity bounds, profiling metrics, technical limitations, and actionable errors.
Applies active ANSI/VT sanitization to all untrusted content and graceful ASCII
fallback for Windows consoles.
"""

from __future__ import annotations

import sys
from typing import TextIO

from localdev.reporting.sanitizer import safe_terminal_encode, sanitize_terminal_text
from localdev.schemas import (
    AnalysisReport,
    DetectionResult,
    ExecutionResult,
    TargetInfoRecord,
    ValidationLevel,
)


class TerminalReporter:
    """Renders structured, sanitized reports to human-facing terminal streams."""

    def __init__(
        self,
        stream: TextIO | None = None,
        enable_sanitization: bool = True,
    ) -> None:
        self.stream: TextIO = stream if stream is not None else sys.stdout
        self.enable_sanitization: bool = enable_sanitization

    def write(self, text: str) -> None:
        """Sanitize, encode, and write text to the reporter's output stream."""
        content = sanitize_terminal_text(text) if self.enable_sanitization else text
        target_encoding = getattr(self.stream, "encoding", None) or "utf-8"
        encoded = safe_terminal_encode(content, target_encoding=target_encoding)
        self.stream.write(encoded)
        self.stream.flush()

    def print_line(self, text: str = "") -> None:
        """Write text followed by a newline."""
        self.write(text + "\n")

    # =========================================================================
    # Section Renderers
    # =========================================================================

    def render_header(self, command: str, target: str | None = None, success: bool = True) -> str:
        """Render top-level command banner."""
        status_tag = "SUCCESS" if success else "FAILED"
        lines = [
            f"=== localdev {command.upper()} [{status_tag}] ===",
        ]
        if target:
            lines.append(f"Target: {target}")
        lines.append("")
        return "\n".join(lines)

    def render_info(self, info: TargetInfoRecord) -> str:
        """Render target file attributes and language detection result."""
        target = info.target
        detection = info.detection

        t_lines = [
            "--- Target File Attributes ---",
            f"  Path:                  {target.path}",
            f"  Absolute Path:         {target.absolute_path}",
            f"  File Size:             {target.file_size_bytes:,} bytes",
            f"  Lines:                 {info.total_lines:,}",
            f"  SHA-256:               {target.sha256}",
            f"  Encoding:              {target.encoding}",
            f"  UTF-8 BOM:             {'Yes' if target.has_bom else 'No'}",
            f"  Newline Convention:    {target.newline_style!r}",
            f"  Trailing Newline:      {'Yes' if target.has_trailing_newline else 'No'}",
            f"  Read-Only Status:      {'Yes' if target.is_read_only else 'No'}",
            f"  Reparse Point:         {'Yes' if target.is_reparse_point else 'No'}",
            "",
            "--- Language Detection ---",
            f"  Language:              {detection.language}",
            f"  Confidence:            {detection.confidence.value}",
            f"  Matched Extension:     {detection.matched_extension or 'None'}",
            f"  Shebang Present:       {'Yes' if detection.has_shebang else 'No'}",
        ]
        if detection.reasons:
            t_lines.append("  Detection Reasons:")
            for r in detection.reasons:
                t_lines.append(f"    • {r}")
        t_lines.append("")

        parts = [
            self.render_header("info", target=target.path, success=True),
            "\n".join(t_lines),
        ]
        full_text = "\n".join(parts)
        return sanitize_terminal_text(full_text) if self.enable_sanitization else full_text

    def render_detect(self, target_path: str, detection: DetectionResult) -> str:
        """Render target language detection result."""
        d_lines = [
            "--- Language Detection ---",
            f"  Language:              {detection.language}",
            f"  Confidence:            {detection.confidence.value}",
            f"  Matched Extension:     {detection.matched_extension or 'None'}",
            f"  Shebang Present:       {'Yes' if detection.has_shebang else 'No'}",
        ]
        if detection.reasons:
            d_lines.append("  Detection Reasons:")
            for r in detection.reasons:
                d_lines.append(f"    • {r}")
        d_lines.append("")

        parts = [
            self.render_header("detect", target=target_path, success=True),
            "\n".join(d_lines),
        ]
        full_text = "\n".join(parts)
        return sanitize_terminal_text(full_text) if self.enable_sanitization else full_text

    def render_analyse(self, report: AnalysisReport) -> str:
        """Render deterministic static analysis report."""
        target = report.target
        success = report.syntax_valid

        parts: list[str] = [
            self.render_header("analyse", target=target.path, success=success),
        ]

        # 1. Syntax Check Section
        syn_lines = ["--- Syntax Check ---"]
        if report.syntax_valid:
            syn_lines.append("  Status:                VALID")
            syn_lines.append(f"  Total Lines:           {report.total_lines:,}")
        else:
            syn_lines.append("  Status:                FAILED")
            syn_lines.append(f"  Total Lines:           {report.total_lines:,}")
            syn_lines.append("  Syntax Errors:")
            for err in report.syntax_diagnostics:
                syn_lines.append(
                    f"    • Line {err.start_line}, Col {err.start_col}: [{err.code}] {err.message}"
                )
            syn_lines.append("  Note: Syntax failure short-circuited AST and linter diagnostics.")
        syn_lines.append("")
        parts.append("\n".join(syn_lines))

        # 2. Structural AST Declarations Section (if syntax valid)
        if report.syntax_valid and report.ast_facts is not None:
            ast_facts = report.ast_facts
            ast_lines = ["--- Structural AST Declarations ---"]

            # Functions / Methods
            if ast_facts.functions:
                ast_lines.append(f"  Functions & Methods ({len(ast_facts.functions)}):")
                for fn in ast_facts.functions:
                    fn_type = "[async] " if fn.is_async else ""
                    kind = "method" if fn.is_method else "function"
                    params_str = ", ".join(fn.parameters)
                    ast_lines.append(
                        f"    • {fn_type}{fn.qualified_name}({params_str}) ({kind}, lines {fn.start_line}-{fn.end_line})"
                    )
            else:
                ast_lines.append("  Functions & Methods:   None")

            # Classes
            if ast_facts.classes:
                ast_lines.append(f"  Classes ({len(ast_facts.classes)}):")
                for cls in ast_facts.classes:
                    methods_str = ", ".join(cls.methods) if cls.methods else "none"
                    ast_lines.append(
                        f"    • {cls.name} (lines {cls.start_line}-{cls.end_line}, methods: {methods_str})"
                    )
            else:
                ast_lines.append("  Classes:               None")

            ast_lines.append("")
            parts.append("\n".join(ast_lines))

        # 3. Static Diagnostics (Ruff) Section (if syntax valid)
        if report.syntax_valid:
            diag_lines = ["--- Static Diagnostics (Ruff) ---"]
            if report.diagnostics:
                diag_lines.append(f"  Total Findings:        {len(report.diagnostics)}")
                diag_lines.append("  Findings:")
                for d in report.diagnostics:
                    sev = d.severity.value if hasattr(d.severity, "value") else str(d.severity)
                    fix_marker = " [fix available]" if d.fix_available else ""
                    diag_lines.append(
                        f"    • Line {d.start_line}, Col {d.start_col}: [{d.code}] {d.message} ({sev}){fix_marker}"
                    )
            else:
                diag_lines.append("  Status:                CLEAN (zero diagnostic findings)")
            diag_lines.append("")
            parts.append("\n".join(diag_lines))

        full_text = "\n".join(parts)
        return sanitize_terminal_text(full_text) if self.enable_sanitization else full_text

    def render_debug(self, target_path: str, result: ExecutionResult) -> str:
        """Render deterministic execution and traceback evidence report."""
        success = (result.exit_code == 0 and not result.timed_out)

        parts: list[str] = [
            self.render_header("debug", target=target_path, success=success),
        ]

        # 1. Execution Summary Section
        summary_lines = ["--- Execution Summary ---"]
        status_str = "SUCCESS" if success else ("TIMED OUT" if result.timed_out else "FAILED")
        summary_lines.append(f"  Status:                {status_str}")
        summary_lines.append(f"  Exit Code:             {result.exit_code}")
        summary_lines.append(f"  Duration:              {result.duration_seconds:.3f}s")
        summary_lines.append(f"  Backend:               {result.execution_backend}")
        if result.peak_process_tree_rss_bytes is not None:
            rss_mb = result.peak_process_tree_rss_bytes / (1024 * 1024)
            summary_lines.append(
                f"  Peak Process RSS:      {rss_mb:.2f} MB (approximate, sampled)"
            )
        if result.timed_out:
            summary_lines.append("  Timed Out:             YES")
        if result.output_truncated:
            summary_lines.append("  Output Truncated:      YES (byte cap breached)")
        summary_lines.append("")
        parts.append("\n".join(summary_lines))

        # 2. Error Signature Section (if present)
        if result.error_signature is not None:
            sig = result.error_signature
            sig_lines = ["--- Runtime Error Signature ---"]
            sig_lines.append(f"  Exception Type:        {sig.exception_type}")
            sig_lines.append(f"  Message:               {sig.normalized_message or '(none)'}")
            if sig.top_target_file:
                site_str = sig.top_target_file
                if sig.top_target_line is not None:
                    site_str += f":{sig.top_target_line}"
                sig_lines.append(f"  Top Target Site:       {site_str}")
            sig_lines.append("")
            parts.append("\n".join(sig_lines))

        # 3. Traceback Frames Section (if frames present)
        if result.frames:
            tb_lines = [f"--- Traceback Frames ({len(result.frames)}) ---"]
            for frame in result.frames:
                scope = f"in {frame.function_name}" if frame.function_name else ""
                origin = "[TARGET]  " if frame.is_target else "[EXTERNAL]"
                tb_lines.append(f"  • {origin} {frame.file_path}:{frame.line_number} {scope}")
                if frame.code_line:
                    tb_lines.append(f"      > {frame.code_line}")
            tb_lines.append("")
            parts.append("\n".join(tb_lines))

        # 4. Captured Output Section
        if result.stdout.strip():
            out_lines = ["--- Standard Output ---"]
            out_lines.append(result.stdout.rstrip())
            out_lines.append("")
            parts.append("\n".join(out_lines))

        if result.stderr.strip() and not result.frames:
            err_lines = ["--- Standard Error ---"]
            err_lines.append(result.stderr.rstrip())
            err_lines.append("")
            parts.append("\n".join(err_lines))

        full_text = "\n".join(parts)
        return sanitize_terminal_text(full_text) if self.enable_sanitization else full_text

    def render_section(self, title: str, body: str) -> str:
        """Render a titled section with indentation."""
        lines = [f"--- {title} ---", body.rstrip(), ""]
        return "\n".join(lines)

    def render_evidence_list(self, title: str, items: list[str]) -> str:
        """Render a bulleted list of evidence facts or diagnostic items."""
        if not items:
            return ""
        lines = [f"--- {title} ---"]
        for item in items:
            lines.append(f"  • {item}")
        lines.append("")
        return "\n".join(lines)

    def render_diff(self, diff_text: str) -> str:
        """Render a unified diff block."""
        if not diff_text.strip():
            return ""
        lines = [
            "--- Proposed Unified Diff ---",
            diff_text.rstrip(),
            "",
        ]
        return "\n".join(lines)

    def render_validation_levels(
        self,
        level_achieved: ValidationLevel | str,
        checks: dict[str, bool | None],
    ) -> str:
        """Render empirical validation tier assessment (Levels A–D)."""
        level_str = level_achieved.value if isinstance(level_achieved, ValidationLevel) else str(level_achieved)
        lines = [
            f"--- Validation Assessment [Highest Level Achieved: {level_str}] ---",
        ]

        check_descriptions = {
            "static_valid": "Level A (Static validity: clean AST parsing, 0 new Ruff diagnostics)",
            "failure_reproduction_removed": "Level B (Failure reproduction removed: original error no longer reproduced)",
            "clean_execution": "Level C (Clean execution: exit code 0 under controlled limits)",
            "behavioral_oracle_passed": "Level D (Behavioral oracle: explicit expected stdout/exit satisfied)",
        }

        for key, description in check_descriptions.items():
            result = checks.get(key)
            if result is True:
                status_glyph = "[OK]"
            elif result is False:
                status_glyph = "[FAIL]"
            else:
                status_glyph = "[-]"
            lines.append(f"  {status_glyph} {description}")

        if level_str in ("B", "Level B"):
            lines.append("  Note: Level B confirms failure signature removal, but does NOT prove correctness.")

        lines.append("")
        return "\n".join(lines)

    def render_complexity_report(
        self,
        target: str,
        time_comp: str,
        aux_space: str,
        output_space: str,
        confidence: str,
        assumptions: list[str],
        details: str = "",
        abstention_reason: str | None = None,
    ) -> str:
        """Render asymptotic complexity bounds and CPython runtime contract assumptions."""
        lines = [
            f"--- Complexity Analysis: {target} ---",
            f"  Time Complexity:       {time_comp}",
            f"  Auxiliary Space:       {aux_space} (transient stack/heap buffers)",
            f"  Output Space:          {output_space} (escaping return structures)",
            f"  Confidence:            {confidence}",
        ]

        if abstention_reason:
            lines.append(f"  Abstention Reason:     {abstention_reason}")

        if assumptions:
            lines.append("  Assumptions:")
            for a in assumptions:
                lines.append(f"    • {a}")

        if details.strip():
            lines.append(f"  Details: {details.strip()}")

        lines.append("")
        return "\n".join(lines)

    def render_profile_report(
        self,
        target: str,
        import_ms: float,
        latency_median_ms: float,
        latency_dispersion_ms: float,
        tracemalloc_bytes: int,
        rss_bytes: int,
        warmup_runs: int,
        measured_runs: int,
        hot_process: bool = True,
    ) -> str:
        """Render function profiling metrics separating import, latency, heap, and RSS."""
        heap_kb = tracemalloc_bytes / 1024.0
        rss_mb = rss_bytes / (1024.0 * 1024.0)

        lines = [
            f"--- Function Profile: {target} ---",
            f"  Import Cost:           {import_ms:.2f} ms",
            f"  Median Latency:        {latency_median_ms:.3f} ms (dispersion: ±{latency_dispersion_ms:.3f} ms)",
            f"  Python Heap (Peak):    {heap_kb:.1f} KB ({tracemalloc_bytes:,} bytes, tracemalloc-tracked)",
            f"  Worker Process RSS:    {rss_mb:.2f} MB ({rss_bytes:,} bytes, peak process tree)",
            f"  Iterations:            {warmup_runs} warm-up, {measured_runs} measured",
            f"  Execution Semantics:   {'Hot process (module state persistent)' if hot_process else 'Fresh process'}",
            "",
        ]
        return "\n".join(lines)

    def render_limitations(self, limitations: list[str]) -> str:
        """Render explicit technical limitations or safe abstentions."""
        if not limitations:
            return ""
        lines = ["--- Limitations & Abstentions ---"]
        for lim in limitations:
            lines.append(f"  [!] {lim}")
        lines.append("")
        return "\n".join(lines)

    def render_errors(self, errors: list[str]) -> str:
        """Render fatal or actionable error messages."""
        if not errors:
            return ""
        lines = ["--- Errors Encountered ---"]
        for err in errors:
            lines.append(f"  [ERROR] {err}")
        lines.append("")
        return "\n".join(lines)

    def render_report(
        self,
        command: str,
        success: bool,
        target_path: str | None = None,
        summary: str | None = None,
        evidence: list[str] | None = None,
        diff: str | None = None,
        validation: tuple[str, dict[str, bool | None]] | None = None,
        limitations: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> str:
        """Assemble a complete sanitized terminal report string."""
        parts: list[str] = [self.render_header(command, target=target_path, success=success)]

        if summary:
            parts.append(summary.rstrip() + "\n")

        if evidence:
            parts.append(self.render_evidence_list("Verified Evidence", evidence))

        if diff:
            parts.append(self.render_diff(diff))

        if validation:
            level, checks = validation
            parts.append(self.render_validation_levels(level, checks))

        if limitations:
            parts.append(self.render_limitations(limitations))

        if errors:
            parts.append(self.render_errors(errors))

        full_text = "\n".join(p for p in parts if p.strip())
        return sanitize_terminal_text(full_text) if self.enable_sanitization else full_text
