"""Integration tests for the complete fix and propose-fix workflows (P8-T4).

Tests:
1. Clean patch application with interactive confirmation.
2. Clean patch application with noninteractive --apply.
3. User declined patch application (target untouched, no backup).
4. Propose-only / --propose-fix workflow without applying.
5. Automated retry on invalid proposal (first attempt invalid, second succeeds).
6. Automated retry on schema validation error (first fails, retry succeeds).
7. Automated retry failure on both attempts leads to honest abstention.
8. Compare-before-replace stale hash detection aborts replacement.
9. Healthy target without defects reports clean status without model invocation.
10. Full CLI dispatch with --apply.
11. Full CLI dispatch with JSON envelope output.
12. Full CLI dispatch with abstention producing EXIT_ABSTENTION (6).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from localdev.agent.orchestrator import Orchestrator
from localdev.agent.permissions import validate_target
from localdev.agent.session import Session
from localdev.cli import main
from localdev.constants import (
    EXIT_ABSTENTION,
    EXIT_SUCCESS,
)
from localdev.errors import SchemaValidationError, StaleEditError
from localdev.inference.base import BaseInferenceClient
from localdev.patching import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.schemas import (
    ConfidenceEnum,
    DiagnosisAbstention,
    DiagnosisRecord,
    InferenceMetadata,
    ValidationLevel,
)


class FakeFixInferenceClient(BaseInferenceClient):
    """Deterministic fake client for fix workflow integration testing."""

    def __init__(
        self,
        diagnosis_record: DiagnosisRecord | DiagnosisAbstention | None = None,
        proposals: Sequence[EditProposalRecord | Exception] | None = None,
        available: bool = True,
    ) -> None:
        self.diagnosis_record = diagnosis_record or DiagnosisRecord(
            bug_description="Division by zero in compute",
            root_cause="Denominator b is 0",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["runtime:ZeroDivisionError"],
            rationale="Evaluating a // b when b=0 raises ZeroDivisionError.",
        )
        self.proposals = list(proposals) if proposals is not None else []
        self.available = available
        self.call_history: list[list[dict[str, str]]] = []

    def is_available(self) -> bool:
        return self.available

    def unload_model(self, model: str | None = None) -> bool:
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
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat_structured(messages, schema, model=model, keep_alive=keep_alive)

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[Any],
        *,
        model: str | None = None,
        keep_alive: int | None = None,
        enforce_prompt_budget: bool = True,
    ) -> tuple[Any, InferenceMetadata]:
        self.call_history.append(messages)
        metadata = InferenceMetadata(
            context_window_tokens=2048,
            prompt_budget_tokens=1200,
            output_budget_tokens=600,
            application_safety_margin_tokens=248,
            estimated_prompt_tokens=220,
            prompt_eval_count=215,
            eval_count=85,
            was_truncated=False,
            omitted_evidence_categories=[],
        )

        if schema is DiagnosisRecord:
            if isinstance(self.diagnosis_record, Exception):
                raise self.diagnosis_record
            return self.diagnosis_record, metadata

        if schema is EditProposalRecord:
            if not self.proposals:
                raise RuntimeError("No remaining proposals configured in FakeFixInferenceClient")
            item = self.proposals.pop(0)
            if isinstance(item, Exception):
                raise item
            return item, metadata

        raise ValueError(f"Unexpected schema requested: {schema}")


BUGGY_SCRIPT = """\
def compute():
    a = 10
    b = 0
    return a // b

if __name__ == "__main__":
    compute()
"""

FIXED_PROPOSAL = EditProposalRecord(
    target_file="sample.py",
    edits=[
        EditOperation(
            operation=EditOperationType.REPLACE,
            start_line=3,
            end_line=3,
            expected_text="    b = 0",
            replacement_text="    b = 2",
        ),
    ],
    explanation="Change denominator b from 0 to 2 to prevent ZeroDivisionError.",
)

INVALID_TEXT_PROPOSAL = EditProposalRecord(
    target_file="sample.py",
    edits=[
        EditOperation(
            operation=EditOperationType.REPLACE,
            start_line=3,
            end_line=3,
            expected_text="    b = 999",  # Does not match target text
            replacement_text="    b = 2",
        ),
    ],
    explanation="Invalid expected text to trigger retry.",
)


@pytest.mark.windows
class TestFixWorkflowIntegration:
    """Integration test suite for the fix and propose-fix workflows."""

    def test_fix_clean_patch_interactive_confirmed(self, tmp_path: Path) -> None:
        """Target file is diagnosed, candidate validated, user confirms, and atomic replacement succeeds."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        prompt_called = False

        def mock_prompt(prompt_text: str) -> bool:
            nonlocal prompt_called
            prompt_called = True
            return True

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                apply=False,
                interactive=True,
                prompt_func=mock_prompt,
                inference_client=client,
            )

        assert prompt_called is True
        assert report.applied is True
        assert report.declined is False
        assert report.abstention is None
        assert report.proposal is not None
        assert report.diff != ""
        assert report.validation is not None
        assert report.validation.static_valid is True
        assert report.validation.clean_execution is True
        assert report.validation.level_achieved in (
            ValidationLevel.LEVEL_B,
            ValidationLevel.LEVEL_C,
        )

        # Verify disk modifications
        new_content = target_file.read_text(encoding="utf-8")
        assert "b = 2" in new_content
        assert "b = 0" not in new_content

        # Verify native backup
        backup_file = tmp_path / "sample.py.bak"
        assert backup_file.is_file()
        assert "b = 0" in backup_file.read_text(encoding="utf-8")

    def test_fix_clean_patch_noninteractive_apply(self, tmp_path: Path) -> None:
        """With apply=True, patch applies without confirmation prompt."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                apply=True,
                inference_client=client,
            )

        assert report.applied is True
        assert report.declined is False
        assert target_file.read_text(encoding="utf-8") == BUGGY_SCRIPT.replace("b = 0", "b = 2")

    def test_fix_user_declined_apply(self, tmp_path: Path) -> None:
        """When user declines interactive prompt, file remains untouched and no backup is created."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                apply=False,
                interactive=True,
                prompt_func=lambda _: False,
                inference_client=client,
            )

        assert report.applied is False
        assert report.declined is True
        assert report.abstention is None
        assert "aborted by user" in report.message.lower() or "declined by user" in report.message.lower()

        # Assert file untouched
        assert target_file.read_text(encoding="utf-8") == BUGGY_SCRIPT
        backup_file = tmp_path / "sample.py.bak"
        assert not backup_file.exists()

    def test_fix_propose_only_flag(self, tmp_path: Path) -> None:
        """With propose_only=True, proposal and validation are rendered but no prompt or write occurs."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                propose_only=True,
                inference_client=client,
            )

        assert report.applied is False
        assert report.declined is False
        assert report.diff != ""
        assert "Fix proposed successfully" in report.message
        assert target_file.read_text(encoding="utf-8") == BUGGY_SCRIPT

    def test_fix_retry_on_invalid_proposal_then_success(self, tmp_path: Path) -> None:
        """First proposal fails text verification, orchestrator retries once and applies second proposal."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[INVALID_TEXT_PROPOSAL, FIXED_PROPOSAL])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                apply=True,
                inference_client=client,
            )

        assert report.applied is True
        assert report.abstention is None
        assert "b = 2" in target_file.read_text(encoding="utf-8")
        # 1 diagnosis call + 2 proposal calls = 3 calls
        assert len(client.call_history) == 3

    def test_fix_retry_on_schema_error_then_success(self, tmp_path: Path) -> None:
        """First proposal raises SchemaValidationError, retry succeeds."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        schema_err = SchemaValidationError("Field edits is missing", raw_payload='{"target_file": "sample.py"}')
        client = FakeFixInferenceClient(proposals=[schema_err, FIXED_PROPOSAL])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                apply=True,
                inference_client=client,
            )

        assert report.applied is True
        assert report.abstention is None
        assert "b = 2" in target_file.read_text(encoding="utf-8")

    def test_fix_retry_on_invalid_proposal_double_failure_abstains(self, tmp_path: Path) -> None:
        """When both attempts produce invalid proposals, fix abstains safely."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[INVALID_TEXT_PROPOSAL, INVALID_TEXT_PROPOSAL])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                apply=True,
                inference_client=client,
            )

        assert report.applied is False
        assert report.abstention is not None
        assert report.abstention.retry_attempted is True
        assert target_file.read_text(encoding="utf-8") == BUGGY_SCRIPT

    def test_fix_stale_hash_abort(self, tmp_path: Path) -> None:
        """External modification before replacement triggers StaleEditError and aborts."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)

            # Externally modify target file to change SHA-256
            target_file.write_text(BUGGY_SCRIPT + "\n# external edit\n", encoding="utf-8")

            with pytest.raises(StaleEditError) as exc_info:
                orchestrator.fix(
                    target=target,
                    apply=True,
                    inference_client=client,
                )

            assert "was modified externally" in exc_info.value.message

    def test_fix_target_healthy_no_defect(self, tmp_path: Path) -> None:
        """Healthy target with no syntax, lint, or runtime defects reports clean status without model."""
        clean_code = "def add(a: int, b: int) -> int:\n    return a + b\n\nif __name__ == '__main__':\n    add(1, 2)\n"
        target_file = tmp_path / "clean.py"
        target_file.write_text(clean_code, encoding="utf-8")
        target = validate_target(target_file)

        client = FakeFixInferenceClient(proposals=[])

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.fix(
                target=target,
                apply=True,
                inference_client=client,
            )

        assert report.applied is False
        assert report.proposal is None
        assert "No defect detected" in report.message
        # No calls to model
        assert len(client.call_history) == 0

    def test_fix_cli_apply_flow(self, tmp_path: Path) -> None:
        """CLI invocation 'localdev fix <target> --apply' applies patch and outputs summary."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        stdout = io.StringIO()
        with (
            patch("localdev.inference.ollama_client.OllamaClient", return_value=client),
            redirect_stdout(stdout),
        ):
            code = main(["fix", str(target_file), "--apply"])

        assert code == EXIT_SUCCESS
        out_text = stdout.getvalue()
        assert "=== localdev FIX [SUCCESS] ===" in out_text
        assert "[APPLIED] Patch successfully applied" in out_text
        assert "b = 2" in target_file.read_text(encoding="utf-8")

    def test_fix_cli_declined_flow(self, tmp_path: Path) -> None:
        """CLI invocation where user inputs 'n' exits 0 with declined message."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        stdout = io.StringIO()
        with (
            patch("localdev.inference.ollama_client.OllamaClient", return_value=client),
            patch("builtins.input", return_value="n"),
            redirect_stdout(stdout),
        ):
            code = main(["fix", str(target_file)])

        assert code == EXIT_SUCCESS
        out_text = stdout.getvalue()
        assert "[DECLINED] Patch application was declined by user" in out_text
        assert target_file.read_text(encoding="utf-8") == BUGGY_SCRIPT

    def test_fix_cli_json_envelope(self, tmp_path: Path) -> None:
        """CLI invocation with --json outputs valid envelope with FixReport payload."""
        import json

        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")

        client = FakeFixInferenceClient(proposals=[FIXED_PROPOSAL])

        stdout = io.StringIO()
        with (
            patch("localdev.inference.ollama_client.OllamaClient", return_value=client),
            redirect_stdout(stdout),
        ):
            code = main(["fix", str(target_file), "--apply", "--json"])

        assert code == EXIT_SUCCESS
        data = json.loads(stdout.getvalue())
        assert data["command"] == "fix"
        assert data["success"] is True
        assert data["data"]["applied"] is True
        assert "sample.py" in data["data"]["diff"]

    def test_fix_cli_abstention_exit_code(self, tmp_path: Path) -> None:
        """When fix workflow abstains, CLI exits with EXIT_ABSTENTION (6)."""
        target_file = tmp_path / "sample.py"
        target_file.write_text(BUGGY_SCRIPT, encoding="utf-8")

        client = FakeFixInferenceClient(available=False)

        stdout = io.StringIO()
        with (
            patch("localdev.inference.ollama_client.OllamaClient", return_value=client),
            redirect_stdout(stdout),
        ):
            code = main(["fix", str(target_file), "--apply"])

        assert code == EXIT_ABSTENTION
        out_text = stdout.getvalue()
        assert "[ABSTAINED]" in out_text
