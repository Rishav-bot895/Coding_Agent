"""Integration test executing the complete demonstration sequence (P12-T5).

Verifies the entire 4-part product demonstration completely offline:
1. File inspection & detection (`info`, `detect`).
2. Static analysis (`analyse`).
3. Subprocess debug execution capturing runtime `IndexError` (`debug`).
4. Static complexity analysis on $O(n^2)$ target (`complexity`).
5. Sound abstention on dynamic while-loop (`complexity` on `collatz_steps`).
6. Hot-process function profiling (`profile`).
7. Guarded patch proposal & validation gates Levels A–D (`fix --propose-only`).
8. Atomic patch application via ReplaceFileW with native backup (`fix --apply`).
9. Clean re-execution of repaired target.
10. Subprocess containment with zero leaked processes.
"""

from __future__ import annotations

import io
import shutil
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil

from localdev.cli import main
from localdev.constants import (
    APPLICATION_SAFETY_MARGIN_TOKENS,
    CONTEXT_WINDOW_TOKENS,
    EXIT_ABSTENTION,
    EXIT_SUCCESS,
    EXIT_TARGET_FAILURE,
    OUTPUT_BUDGET_TOKENS,
    PROMPT_BUDGET_TOKENS,
)
from localdev.inference.base import BaseInferenceClient
from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.schemas import (
    ConfidenceEnum,
    DiagnosisRecord,
    InferenceMetadata,
)


class MockDemoInferenceClient(BaseInferenceClient):
    """Deterministic offline inference client for demo repair workflow."""

    def __init__(self, target_filename: str) -> None:
        self.target_filename = target_filename
        self.call_history: list[str] = []
        self.unloaded: bool = False

    def is_available(self) -> bool:
        return True

    def unload_model(self, model: str | None = None) -> bool:
        self.unloaded = True
        return True

    def generate_structured(
        self,
        prompt: str,
        schema: type[Any],
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        keep_alive: int | None = None,
        enforce_prompt_budget: bool = True,
    ) -> tuple[Any, InferenceMetadata]:
        self.call_history.append(f"generate:{schema.__name__}")

        meta = InferenceMetadata(
            prompt_budget_tokens=PROMPT_BUDGET_TOKENS,
            output_budget_tokens=OUTPUT_BUDGET_TOKENS,
            application_safety_margin_tokens=APPLICATION_SAFETY_MARGIN_TOKENS,
            context_window_tokens=CONTEXT_WINDOW_TOKENS,
            estimated_prompt_tokens=420,
            prompt_eval_count=420,
            eval_count=130,
            was_truncated=False,
            eval_duration_ms=380.0,
        )

        if schema is DiagnosisRecord:
            record = DiagnosisRecord(
                bug_description="IndexError in process_scores due to range(1, len(scores) + 1).",
                root_cause=(
                    "The loop range(1, len(scores) + 1) accesses scores[len(scores)] "
                    "on the final iteration, which exceeds the zero-based list bound."
                ),
                confidence=ConfidenceEnum.HIGH,
                cited_evidence_ids=["traceback:line_8"],
                rationale="Traceback identifies line 8 as raising IndexError.",
            )
            return record, meta

        if schema is EditProposalRecord:
            proposal = EditProposalRecord(
                target_file=self.target_filename,
                explanation="Fix off-by-one loop bound by iterating over range(len(scores)).",
                edits=[
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=7,
                        end_line=7,
                        expected_text="    for i in range(1, len(scores) + 1):",
                        replacement_text="    for i in range(len(scores)):",
                    )
                ],
            )
            return proposal, meta

        raise ValueError(f"Unexpected schema: {schema}")

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[Any],
        *,
        model: str | None = None,
        keep_alive: int | None = None,
        enforce_prompt_budget: bool = True,
    ) -> tuple[Any, InferenceMetadata]:
        prompt = "\n".join(m.get("content", "") for m in messages)
        return self.generate_structured(
            prompt,
            schema,
            model=model,
            keep_alive=keep_alive,
            enforce_prompt_budget=enforce_prompt_budget,
        )


def test_complete_demo_sequence(tmp_path: Path) -> None:
    """Execute the complete four-part demonstration sequence on an isolated fixture copy."""
    initial_children = {p.pid for p in psutil.Process().children(recursive=True)}

    # Copy demo files to isolated temporary workspace
    demo_source = Path("examples/demo.py")
    input_source = Path("examples/demo_input.json")
    assert demo_source.exists(), "examples/demo.py must exist"
    assert input_source.exists(), "examples/demo_input.json must exist"

    target = tmp_path / "demo.py"
    shutil.copyfile(demo_source, target)
    input_file = tmp_path / "demo_input.json"
    shutil.copyfile(input_source, input_file)

    initial_content = target.read_text(encoding="utf-8")
    assert "range(1, len(scores) + 1)" in initial_content

    # -------------------------------------------------------------------------
    # Part 0: Inspect & Detect
    # -------------------------------------------------------------------------
    out_info = io.StringIO()
    with redirect_stdout(out_info):
        code_info = main(["info", str(target), "--json"])
    assert code_info == EXIT_SUCCESS
    assert '"command": "info"' in out_info.getvalue()
    assert '"language": "python"' in out_info.getvalue()

    out_detect = io.StringIO()
    with redirect_stdout(out_detect):
        code_detect = main(["detect", str(target), "--json"])
    assert code_detect == EXIT_SUCCESS
    assert '"confidence": "CERTAIN"' in out_detect.getvalue()

    # -------------------------------------------------------------------------
    # Part 1.0: Static Analysis
    # -------------------------------------------------------------------------
    out_analyse = io.StringIO()
    with redirect_stdout(out_analyse):
        code_analyse = main(["analyse", str(target), "--json"])
    assert code_analyse == EXIT_SUCCESS
    assert '"syntax_valid": true' in out_analyse.getvalue()

    # -------------------------------------------------------------------------
    # Part 1.1: Debug Failure Reproduction
    # -------------------------------------------------------------------------
    out_debug = io.StringIO()
    with redirect_stdout(out_debug):
        code_debug = main(["debug", str(target), "--json"])
    assert code_debug == EXIT_TARGET_FAILURE  # Returns 1 on target failure
    debug_payload = out_debug.getvalue()
    assert '"exception_type": "IndexError"' in debug_payload
    assert "list index out of range" in debug_payload

    # -------------------------------------------------------------------------
    # Part 2: Static Complexity Analysis (O(n^2) on find_target_pairs)
    # -------------------------------------------------------------------------
    out_comp = io.StringIO()
    with redirect_stdout(out_comp):
        code_comp = main(["complexity", f"{target}::find_target_pairs", "--json"])
    assert code_comp == EXIT_SUCCESS
    comp_payload = out_comp.getvalue()
    has_n2 = (
        '"time_complexity": "O(n\\u00b2)"' in comp_payload
        or '"time_complexity": "O(n²)"' in comp_payload
    )
    assert has_n2
    assert '"auxiliary_space": "O(1)"' in comp_payload
    assert '"output_space": "O(n)"' in comp_payload

    # -------------------------------------------------------------------------
    # Part 4 (Early Check): Honest Abstention (DYNAMIC_BOUNDS on collatz_steps)
    # -------------------------------------------------------------------------
    out_collatz = io.StringIO()
    with redirect_stdout(out_collatz):
        code_collatz = main(["complexity", f"{target}::collatz_steps", "--json"])
    assert code_collatz == EXIT_ABSTENTION
    # Command reports abstention in JSON payload
    collatz_payload = out_collatz.getvalue()
    assert '"time_complexity": "UNKNOWN"' in collatz_payload
    assert "DYNAMIC_BOUNDS" in collatz_payload

    # -------------------------------------------------------------------------
    # Part 3: Hot-Process Function Profiling
    # -------------------------------------------------------------------------
    out_prof = io.StringIO()
    with redirect_stdout(out_prof):
        code_prof = main([
            "profile",
            f"{target}::find_target_pairs",
            "--input",
            str(input_file),
            "--warmup",
            "2",
            "--measured",
            "5",
            "--json",
        ])
    assert code_prof == EXIT_SUCCESS
    prof_payload = out_prof.getvalue()
    assert '"measured_invocations": 5' in prof_payload
    assert '"hot_process_reused": true' in prof_payload
    assert '"median_latency_ms"' in prof_payload

    # -------------------------------------------------------------------------
    # Part 1.2: Propose Fix (Propose-Only Review)
    # -------------------------------------------------------------------------
    mock_client = MockDemoInferenceClient(target_filename=target.name)
    out_propose = io.StringIO()
    with patch(
        "localdev.inference.ollama_client.OllamaClient", return_value=mock_client
    ), redirect_stdout(out_propose):
        code_propose = main([
            "fix",
            str(target),
            "--propose-only",
            "--expected-stdout-contains",
            "Total Score: 150",
            "--json",
        ])
    assert code_propose == EXIT_SUCCESS
    propose_payload = out_propose.getvalue()
    assert '"applied": false' in propose_payload
    assert '"level_achieved": "D"' in propose_payload
    assert '"static_valid": true' in propose_payload
    assert '"failure_reproduction_removed": true' in propose_payload
    assert '"clean_execution": true' in propose_payload
    assert '"behavioral_oracle_passed": true' in propose_payload


    # Confirm target was NOT modified
    assert target.read_text(encoding="utf-8") == initial_content

    # -------------------------------------------------------------------------
    # Part 1.3: Apply Fix Atomically with Native Backup
    # -------------------------------------------------------------------------
    out_apply = io.StringIO()
    with patch(
        "localdev.inference.ollama_client.OllamaClient", return_value=mock_client
    ), redirect_stdout(out_apply):
        code_apply = main([
            "fix",
            str(target),
            "--apply",
            "--expected-stdout-contains",
            "Total Score: 150",
            "--json",
        ])
    assert code_apply == EXIT_SUCCESS
    apply_payload = out_apply.getvalue()
    assert '"applied": true' in apply_payload

    # Verify atomic replacement and backup creation on same volume
    backup_file = target.with_name(f"{target.name}.bak")
    assert backup_file.exists(), f"Backup file '{backup_file}' must exist after --apply"
    assert "range(1, len(scores) + 1)" in backup_file.read_text(encoding="utf-8")

    # Verify repaired target contents
    repaired_content = target.read_text(encoding="utf-8")
    assert "for i in range(len(scores)):" in repaired_content
    assert "for i in range(1, len(scores) + 1):" not in repaired_content

    # -------------------------------------------------------------------------
    # Part 1.4: Re-run Debug on Repaired Target
    # -------------------------------------------------------------------------
    out_rerun = io.StringIO()
    with redirect_stdout(out_rerun):
        code_rerun = main(["debug", str(target), "--json"])
    assert code_rerun == EXIT_SUCCESS
    rerun_payload = out_rerun.getvalue()
    assert '"exit_code": 0' in rerun_payload
    assert "Total Score: 150" in rerun_payload

    # -------------------------------------------------------------------------
    # Process Cleanup Audit
    # -------------------------------------------------------------------------
    final_children = {p.pid for p in psutil.Process().children(recursive=True)}
    leaked = final_children - initial_children
    assert len(leaked) == 0, f"Leaked processes detected: {leaked}"
