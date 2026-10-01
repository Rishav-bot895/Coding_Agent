"""Unit tests for instruction-tuning dataset preparation, schemas, and verification (P13-T1)."""

from __future__ import annotations

import ast
import json

import pytest

from localdev.patching.applier import PatchApplier
from localdev.patching.edit_schema import (
    EditProposalRecord,
)
from localdev.schemas import (
    ConfidenceEnum,
    DiagnosisAbstention,
    DiagnosisAbstentionReason,
    DiagnosisRecord,
)
from tools.finetune.prepare_dataset import (
    DEFAULT_OUTPUT_DIR,
    DatasetGenerator,
    create_stratified_splits,
    validate_dataset,
)
from tools.finetune.schema_templates import (
    MAX_COMPLETION_TOKENS,
    MAX_CONTEXT_WINDOW,
    MAX_PROMPT_TOKENS,
    DatasetSampleType,
    InstructionPair,
    QwenTokenCounter,
    build_abstention_prompt_messages,
    build_diagnosis_prompt_messages,
    build_edit_proposal_prompt_messages,
    validate_abstention_completion,
    validate_diagnosis_completion,
    validate_edit_completion,
    validate_raw_json_no_markdown,
)


class TestQwenTokenCounter:
    """Tests for QwenTokenCounter BPE tokenizer and bounds."""

    def test_counter_initialization_and_encoding(self) -> None:
        counter = QwenTokenCounter()
        text = "def hello_world() -> str:\n    return 'hello offline agent'\n"
        tokens = counter.count_tokens(text)
        assert tokens > 0
        assert tokens < 50

    def test_empty_string_token_count(self) -> None:
        counter = QwenTokenCounter()
        assert counter.count_tokens("") == 0


class TestPromptBuilders:
    """Tests for standardized prompt builders mirroring ContextBuilder."""

    def test_diagnosis_prompt_contains_manifest_and_delimiters(self) -> None:
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        manifest_items = [
            ("runtime:ZeroDivisionError", "Division by zero on line 2"),
            ("traceback:line_2", "Frame at line 2"),
        ]
        messages, prompt, ids = build_diagnosis_prompt_messages(
            target_file="math_test.py",
            source_code=code,
            evidence_items=manifest_items,
            failure_lines=["ZeroDivisionError: division by zero"],
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Available Evidence Manifest" in prompt
        assert "[runtime:ZeroDivisionError]" in prompt
        assert "<<<BEGIN UNTRUSTED TARGET SOURCE CODE>>>" in prompt
        assert "<<<END UNTRUSTED TARGET SOURCE CODE>>>" in prompt
        assert "DiagnosisRecord" in prompt
        assert ids == ["runtime:ZeroDivisionError", "traceback:line_2"]

    def test_edit_proposal_prompt_contains_diagnosis_and_rules(self) -> None:
        code = "def divide(a: float, b: float) -> float:\n    return a / b\n"
        messages, prompt = build_edit_proposal_prompt_messages(
            target_file="math_test.py",
            source_code=code,
            diagnosis_summary="ZeroDivisionError when b is zero.",
            failure_evidence=["Line 2: division by zero"],
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "EditProposalRecord" in prompt
        assert "ZeroDivisionError when b is zero" in prompt
        assert "<<<BEGIN UNTRUSTED TARGET SOURCE CODE>>>" in prompt

    def test_abstention_prompt_for_clean_code(self) -> None:
        code = "def clean_fn() -> int:\n    return 42\n"
        manifest_items = [("runtime:clean", "Exit code 0")]
        messages, prompt, ids = build_abstention_prompt_messages(
            target_file="clean.py",
            source_code=code,
            evidence_items=manifest_items,
            failure_lines=["Clean execution (exit code 0)."],
        )
        assert len(messages) == 2
        assert "DiagnosisAbstention" in prompt
        assert ids == ["runtime:clean"]


class TestSchemaValidation:
    """Tests enforcing zero-markdown JSON, evidence grounding, and edit safety."""

    def test_rejects_markdown_code_fences(self) -> None:
        fenced_payload = '```json\n{"bug_description": "test"}\n```'
        is_raw, _, errors = validate_raw_json_no_markdown(fenced_payload)
        assert not is_raw
        assert any("markdown code fences" in e for e in errors)

    def test_rejects_conversational_preamble(self) -> None:
        preamble_payload = 'Here is the diagnosis:\n{"bug_description": "test"}'
        is_raw, _, errors = validate_raw_json_no_markdown(preamble_payload)
        assert not is_raw
        assert any("No conversational preamble" in e for e in errors)

    def test_validate_diagnosis_rejects_ungrounded_evidence(self) -> None:
        payload = json.dumps({
            "bug_description": "Unchecked zero divisor",
            "root_cause": "Divisor is zero",
            "confidence": "HIGH",
            "cited_evidence_ids": ["runtime:ZeroDivisionError", "hallucinated:evidence_99"],
            "rationale": "Evidence indicates zero division.",
        })
        manifest = ["runtime:ZeroDivisionError", "traceback:line_5"]
        is_valid, record, errors = validate_diagnosis_completion(payload, manifest)
        assert not is_valid
        assert record is None
        assert any("Ungrounded evidence cited" in e for e in errors)

    def test_validate_diagnosis_accepts_grounded_evidence(self) -> None:
        payload = json.dumps({
            "bug_description": "Unchecked zero divisor",
            "root_cause": "Divisor is zero",
            "confidence": "HIGH",
            "cited_evidence_ids": ["runtime:ZeroDivisionError"],
            "rationale": "Evidence indicates zero division.",
        })
        manifest = ["runtime:ZeroDivisionError", "traceback:line_5"]
        is_valid, record, errors = validate_diagnosis_completion(payload, manifest)
        assert is_valid
        assert record is not None
        assert record.confidence == ConfidenceEnum.HIGH

    def test_validate_edit_rejects_mismatched_expected_text(self) -> None:
        source = "def foo() -> int:\n    return 42\n"
        proposal = {
            "target_file": "foo.py",
            "edits": [
                {
                    "operation": "replace",
                    "start_line": 2,
                    "end_line": 2,
                    "expected_text": "    return 100",  # Actual is 42
                    "replacement_text": "    return 0",
                }
            ],
            "explanation": "Fix return value",
        }
        is_valid, rec, errors = validate_edit_completion(json.dumps(proposal), source, "foo.py")
        assert not is_valid
        assert rec is None
        assert any("mismatch" in e.lower() for e in errors)

    def test_validate_edit_accepts_clean_patch(self) -> None:
        source = "def foo() -> int:\n    return 42\n"
        proposal = {
            "target_file": "foo.py",
            "edits": [
                {
                    "operation": "replace",
                    "start_line": 2,
                    "end_line": 2,
                    "expected_text": "    return 42",
                    "replacement_text": "    return 0",
                }
            ],
            "explanation": "Fix return value",
        }
        is_valid, rec, errors = validate_edit_completion(json.dumps(proposal), source, "foo.py")
        assert is_valid
        assert rec is not None
        assert len(rec.edits) == 1

    def test_validate_abstention_rejects_target_mismatch(self) -> None:
        payload = json.dumps({
            "target": "other.py",
            "reason": "UNGROUNDED_EVIDENCE",
            "details": "Clean code",
            "raw_payload": None,
            "validation_errors": [],
            "retry_attempted": False,
        })
        is_valid, abst, errors = validate_abstention_completion(payload, "target.py")
        assert not is_valid
        assert any("Target mismatch" in e for e in errors)


class TestDatasetGenerationPipeline:
    """Tests for batch generation and deterministic verification."""

    def test_small_batch_generation_and_validation(self) -> None:
        generator = DatasetGenerator(seed=123)
        pairs = generator.generate_all_pairs(total_target=30)
        assert len(pairs) == 30

        is_valid, errors = validate_dataset(pairs, generator.token_counter)
        assert is_valid, f"Validation errors: {errors}"

        train, val, test = create_stratified_splits(pairs, seed=123)
        assert len(train) > 0
        assert len(val) > 0
        assert len(test) > 0
        assert len(train) + len(val) + len(test) == 30

    def test_token_budget_assertions_on_generated_pairs(self) -> None:
        generator = DatasetGenerator(seed=999)
        pairs = generator.generate_all_pairs(total_target=20)
        for pair in pairs:
            assert pair.prompt_tokens <= MAX_PROMPT_TOKENS
            assert pair.completion_tokens <= MAX_COMPLETION_TOKENS
            assert pair.total_tokens <= MAX_CONTEXT_WINDOW
            assert pair.prompt_tokens + pair.completion_tokens == pair.total_tokens


@pytest.fixture(scope="module")
def manifest_data() -> dict:
    manifest_file = DEFAULT_OUTPUT_DIR / "manifest.json"
    assert manifest_file.is_file(), f"Manifest not found at {manifest_file}"
    return json.loads(manifest_file.read_text(encoding="utf-8"))


class TestPersistedDatasetManifestAndSplits:
    """Tests verifying the full persisted dataset in datasets/finetune/."""

    def test_manifest_total_and_split_counts(self, manifest_data: dict) -> None:
        total = manifest_data["total_samples"]
        # Acceptance requirement: at least 3,000 high-quality training pairs
        assert total >= 3000

        splits = manifest_data["splits"]
        train_count = splits["train"]["count"]
        val_count = splits["val"]["count"]
        test_count = splits["test"]["count"]

        assert train_count >= 2400
        assert train_count + val_count + test_count == total

        # Check 80/10/10 split bounds
        assert 78.0 <= splits["train"]["percentage"] <= 82.0
        assert 8.0 <= splits["val"]["percentage"] <= 12.0
        assert 8.0 <= splits["test"]["percentage"] <= 12.0

    def test_category_distribution_coverage(self, manifest_data: dict) -> None:
        cat_dist = manifest_data["category_distribution"]
        expected_categories = [
            "ZeroDivisionError",
            "IndexError",
            "KeyError",
            "TypeError",
            "AttributeError",
            "ValueError",
            "NameError",
            "RecursionError",
            "SyntaxError",
            "logic_bug",
            "clean_code",
            "external_dependency",
            "out_of_bounds",
            "prompt_budget_exceeded",
        ]
        for cat in expected_categories:
            assert cat in cat_dist, f"Category '{cat}' missing from distribution"
            assert cat_dist[cat] > 0

    def test_token_statistics_within_invariants(self, manifest_data: dict) -> None:
        stats = manifest_data["token_statistics"]
        assert stats["invariants"]["violations"] == 0
        assert stats["prompt_tokens"]["max"] <= MAX_PROMPT_TOKENS
        assert stats["completion_tokens"]["max"] <= MAX_COMPLETION_TOKENS
        assert stats["total_tokens"]["max"] <= MAX_CONTEXT_WINDOW

    def test_verification_summary_perfect(self, manifest_data: dict) -> None:
        verif = manifest_data["verification_summary"]
        assert verif["schema_compliance_rate"] == 1.0
        assert verif["evidence_grounding_rate"] == 1.0
        assert verif["edit_applicability_rate"] == 1.0
        assert verif["markdown_fence_violations"] == 0
        assert verif["all_verified"] is True

    def test_sample_files_integrity(self) -> None:
        """Spot-check samples from each split file to verify Pydantic parsing and AST validity."""
        for split_name in ["train.jsonl", "val.jsonl", "test.jsonl"]:
            split_path = DEFAULT_OUTPUT_DIR / split_name
            assert split_path.is_file()

            with open(split_path, encoding="utf-8") as f:
                lines = f.readlines()
                assert len(lines) > 0

                # Validate first 25 samples of each split thoroughly
                for line in lines[:25]:
                    pair = InstructionPair.model_validate_json(line)
                    assert pair.prompt_tokens <= MAX_PROMPT_TOKENS
                    assert pair.completion_tokens <= MAX_COMPLETION_TOKENS
                    assert pair.total_tokens <= MAX_CONTEXT_WINDOW
                    assert "```" not in pair.completion

                    if pair.task_type == DatasetSampleType.DIAGNOSIS:
                        rec = DiagnosisRecord.model_validate_json(pair.completion)
                        for eid in rec.cited_evidence_ids:
                            assert eid in pair.evidence_manifest
                    elif pair.task_type == DatasetSampleType.EDIT_PROPOSAL:
                        prop = EditProposalRecord.model_validate_json(pair.completion)
                        assert len(prop.edits) >= 1
                        # Verify patched AST
                        applier = PatchApplier()
                        patched_lines = applier.apply_to_lines(
                            pair.source_code.splitlines(), prop.edits
                        )
                        ast.parse("\n".join(patched_lines))
                    elif pair.task_type == DatasetSampleType.ABSTENTION:
                        abst = DiagnosisAbstention.model_validate_json(pair.completion)
                        assert abst.reason in DiagnosisAbstentionReason
