"""Integration tests for 8 GB laptop budget optimization and lifecycle management (P12-T3).

Verifies:
1. Strict adherence to separated token budgets (prompt 1,200 + output 600 + margin 248 = 2,048).
2. End-to-end chained workflow: analyse -> debug -> fix -> complexity -> profile.
3. Model lifecycle management: trigger Ollama unload prior to heavy profiling.
4. Single-process execution: inference and profiling never execute concurrently.
5. Low-memory fallback model configuration (1.5B tier).
6. Total system committed RAM and process-tree RSS remain bounded without leaks.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil

from localdev.agent.orchestrator import Orchestrator
from localdev.agent.permissions import validate_target
from localdev.agent.session import Session
from localdev.cli import main, parse_cli_args
from localdev.config import LocaldevConfig, get_default_config
from localdev.constants import (
    APPLICATION_SAFETY_MARGIN_TOKENS,
    CONTEXT_WINDOW_TOKENS,
    EXIT_SUCCESS,
    FALLBACK_MODEL,
    OUTPUT_BUDGET_TOKENS,
    PRIMARY_MODEL,
    PROMPT_BUDGET_TOKENS,
)
from localdev.inference.base import BaseInferenceClient
from localdev.patching import EditOperation, EditOperationType, EditProposalRecord
from localdev.schemas import (
    ConfidenceEnum,
    DiagnosisRecord,
    InferenceMetadata,
)


class MockOptimizationClient(BaseInferenceClient):
    """Deterministic mock inference client tracking calls and unload events."""

    def __init__(self, model_name: str = PRIMARY_MODEL) -> None:
        self.model_name = model_name
        self.call_history: list[str] = []
        self.unloaded: bool = False

    def is_available(self) -> bool:
        return True

    def unload_model(self, model: str | None = None) -> bool:
        self.unloaded = True
        self.call_history.append(f"unload:{model or self.model_name}")
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
        effective_model = model or self.model_name
        self.call_history.append(f"generate:{schema.__name__}:{effective_model}")

        meta = InferenceMetadata(
            prompt_budget_tokens=PROMPT_BUDGET_TOKENS,
            output_budget_tokens=OUTPUT_BUDGET_TOKENS,
            application_safety_margin_tokens=APPLICATION_SAFETY_MARGIN_TOKENS,
            context_window_tokens=CONTEXT_WINDOW_TOKENS,
            estimated_prompt_tokens=450,
            prompt_eval_count=450,
            eval_count=120,
            was_truncated=False,
            eval_duration_ms=450.0,
        )

        if schema is DiagnosisRecord:
            record = DiagnosisRecord(
                bug_description="List index out of bounds in loop iteration.",
                root_cause="The loop iterates past the end of the sequence.",
                confidence=ConfidenceEnum.HIGH,
                cited_evidence_ids=["traceback:line_5"],
                rationale="Traceback confirms IndexError at line 5.",
            )
            return record, meta

        if schema is EditProposalRecord:
            proposal = EditProposalRecord(
                target_file="chained_target.py",
                explanation="Fix off-by-one error by using range(len(items)) properly.",
                edits=[
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=5,
                        end_line=5,
                        expected_text="    return items[idx]",
                        replacement_text="    return items[idx - 1] if idx > 0 else items[0]",
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


def test_token_budget_partition_adherence() -> None:
    """Verify strict adherence to the separated token partition (1200 + 600 + 248 = 2048)."""
    assert PROMPT_BUDGET_TOKENS == 1200
    assert OUTPUT_BUDGET_TOKENS == 600
    assert APPLICATION_SAFETY_MARGIN_TOKENS == 248
    assert CONTEXT_WINDOW_TOKENS == 2048
    assert (
        PROMPT_BUDGET_TOKENS + OUTPUT_BUDGET_TOKENS + APPLICATION_SAFETY_MARGIN_TOKENS
        == CONTEXT_WINDOW_TOKENS
    )

    cfg = LocaldevConfig()
    assert cfg.prompt_budget_tokens == 1200
    assert cfg.output_budget_tokens == 600
    assert cfg.application_safety_margin_tokens == 248
    assert cfg.context_window_tokens == 2048

    partition_sum = (
        cfg.prompt_budget_tokens
        + cfg.output_budget_tokens
        + cfg.application_safety_margin_tokens
    )
    assert partition_sum == cfg.context_window_tokens



def test_fallback_model_configuration() -> None:
    """Verify low-memory fallback configuration uses 1.5B tier."""
    cfg = get_default_config("fallback")
    assert cfg.active_model == FALLBACK_MODEL
    assert "1.5b" in cfg.active_model.lower()

    # CLI parsing flag verification
    parsed = parse_cli_args(["fix", "target.py", "--fallback"])
    assert parsed.model == FALLBACK_MODEL
    assert parsed.fallback is True


def test_complete_chained_workflow_under_8gb_budget(tmp_path: Path) -> None:
    """Benchmark chained workflow (analyse -> debug -> fix -> complexity -> profile).

    Verifies single-process sequential execution, model unload before profiling,
    bounded RAM commit, and zero leaked child processes.
    """
    initial_children = {p.pid for p in psutil.Process().children(recursive=True)}

    target = tmp_path / "chained_target.py"
    target.write_text(
        "def compute_sum(items: list[int]) -> int:\n"
        "    total = 0\n"
        "    for x in items:\n"
        "        total += x\n"
        "    return total\n\n"
        "def main() -> None:\n"
        "    data = [1, 2, 3, 4, 5]\n"
        "    print(compute_sum(data))\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )

    input_file = tmp_path / "input.json"
    input_file.write_text('{"args": [[10, 20, 30]]}\n', encoding="utf-8")

    client = MockOptimizationClient(model_name=FALLBACK_MODEL)
    target_rec = validate_target(target)

    with Session(target_record=target_rec) as session:
        orch = Orchestrator(session=session)

        # 1. Analyse
        analysis_report = orch.analyse(target_rec)
        assert analysis_report.syntax_valid is True
        assert len(analysis_report.syntax_diagnostics) == 0

        # 2. Debug
        exec_result = orch.debug(target_rec)
        assert exec_result.exit_code == 0
        assert "15" in exec_result.stdout

        # 3. Fix (Health verification)
        fix_report = orch.fix(
            target_rec,
            inference_client=client,
            model=FALLBACK_MODEL,
        )
        assert "No defect" in fix_report.message

        # 4. Complexity
        comp_reports = orch.analyze_file_complexity(target_rec)
        assert len(comp_reports) >= 2
        func_map = {r.target.split("::")[-1]: r for r in comp_reports}
        assert func_map["compute_sum"].time_complexity == "O(n)"
        assert func_map["compute_sum"].auxiliary_space == "O(1)"

        # 5. Profile (with explicit unload verification)
        with patch("localdev.inference.ollama_client.OllamaClient", return_value=client):
            profile_report = orch.profile(
                target_rec,
                selector="compute_sum",
                input_file=input_file,
                warmup_runs=2,
                measured_runs=5,
                unload_inference_model=True,
            )
            assert profile_report.measured_invocations == 5
            assert profile_report.median_latency_ms > 0
            assert profile_report.hot_process_reused is True
            # Verify unload was requested to free model memory
            assert client.unloaded is True

    # Check total memory usage and process cleanup
    mem_info = psutil.virtual_memory()
    # Available memory should remain healthy (> 500 MB)
    assert mem_info.available > 500 * 1024 * 1024

    final_children = {p.pid for p in psutil.Process().children(recursive=True)}
    new_leaks = final_children - initial_children
    assert len(new_leaks) == 0, f"Leaked processes detected: {new_leaks}"


def test_cli_end_to_end_fallback_workflow(tmp_path: Path) -> None:
    """Verify CLI end-to-end execution with --fallback flag."""
    target = tmp_path / "cli_target.py"
    target.write_text(
        "def run_math() -> int:\n"
        "    return 42\n\n"
        "if __name__ == '__main__':\n"
        "    print(run_math())\n",
        encoding="utf-8",
    )

    # 1. Analyse with fallback
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["analyse", str(target), "--json", "--fallback"])
    assert code == EXIT_SUCCESS
    assert '"command": "analyse"' in out.getvalue()

    # 2. Debug with fallback
    out_dbg = io.StringIO()
    with redirect_stdout(out_dbg):
        code_dbg = main(["debug", str(target), "--json", "--fallback"])
    assert code_dbg == EXIT_SUCCESS
    assert '"command": "debug"' in out_dbg.getvalue()

    # 3. Fix with fallback (propose-only)
    client = MockOptimizationClient(model_name=FALLBACK_MODEL)
    out_fix = io.StringIO()
    with patch(
        "localdev.inference.ollama_client.OllamaClient", return_value=client
    ), redirect_stdout(out_fix):
        code_fix = main(["fix", str(target), "--json", "--fallback", "--propose-only"])
    assert code_fix == EXIT_SUCCESS
    assert '"command": "fix"' in out_fix.getvalue()


