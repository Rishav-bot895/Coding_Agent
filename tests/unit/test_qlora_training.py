"""Unit tests for QLoRA training pipeline, loss masking, and checkpoint verification (P13-T2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from peft import PeftModel
from pydantic import ValidationError
from transformers import AutoTokenizer

from tools.finetune.config import DEFAULT_DATASET_DIR, DEFAULT_OUTPUT_DIR, QLoRAConfig
from tools.finetune.prepare_dataset import DatasetGenerator
from tools.finetune.train_qlora import (
    InstructionDataset,
    build_toy_base_model,
    collate_instruction_batch,
    format_chatml_messages,
    run_smoke_test,
    verify_checkpoint,
)


@pytest.fixture(scope="module")
def qwen_tokenizer() -> AutoTokenizer:
    """Fixture providing AutoTokenizer for Qwen2.5-Coder."""
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-Coder-1.5B-Instruct")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or "<|endoftext|>"
    return tokenizer


class TestQLoRAConfig:
    """Tests for QLoRAConfig Pydantic model and hyperparameters."""

    def test_default_hyperparameters(self) -> None:
        cfg = QLoRAConfig()

        # LoRA architecture
        assert cfg.lora_r == 32
        assert cfg.lora_alpha == 64
        assert cfg.lora_dropout == 0.05
        assert cfg.target_modules == [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ]

        # 4-bit Quantization
        assert cfg.load_in_4bit is True
        assert cfg.bnb_4bit_quant_type == "nf4"
        assert cfg.bnb_4bit_use_double_quant is True
        assert cfg.bnb_4bit_compute_dtype == "bfloat16"

        # Training batch size & schedule
        assert cfg.per_device_train_batch_size == 2
        assert cfg.gradient_accumulation_steps == 8
        assert cfg.effective_batch_size == 16
        assert cfg.learning_rate == 2e-4
        assert cfg.lr_scheduler_type == "cosine"
        assert cfg.warmup_ratio == 0.03
        assert cfg.max_grad_norm == 1.0

        # Token budgets
        assert cfg.max_seq_length == 2048
        assert cfg.max_prompt_length == 1200
        assert cfg.max_completion_length == 600
        assert cfg.mask_prompt_loss is True

        # Paths
        assert cfg.output_dir == DEFAULT_OUTPUT_DIR
        assert cfg.train_file == DEFAULT_DATASET_DIR / "train.jsonl"
        assert cfg.val_file == DEFAULT_DATASET_DIR / "val.jsonl"
        assert cfg.test_file == DEFAULT_DATASET_DIR / "test.jsonl"

    def test_effective_batch_size_calculation(self) -> None:
        cfg = QLoRAConfig(per_device_train_batch_size=4, gradient_accumulation_steps=4)
        assert cfg.effective_batch_size == 16

        cfg2 = QLoRAConfig(per_device_train_batch_size=1, gradient_accumulation_steps=16)
        assert cfg2.effective_batch_size == 16

    def test_config_serialization_roundtrip(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "test_config.json"
        cfg = QLoRAConfig(
            lora_r=16,
            lora_alpha=32,
            learning_rate=1e-4,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=4,
        )
        cfg.save_json(cfg_file)

        assert cfg_file.is_file()
        loaded = QLoRAConfig.load_json(cfg_file)

        assert loaded.lora_r == 16
        assert loaded.lora_alpha == 32
        assert loaded.learning_rate == 1e-4
        assert loaded.effective_batch_size == 4

    def test_validation_bounds(self) -> None:
        with pytest.raises(ValidationError):
            QLoRAConfig(lora_r=0)  # ge=1

        with pytest.raises(ValidationError):
            QLoRAConfig(lora_r=256)  # le=128

        with pytest.raises(ValidationError):
            QLoRAConfig(learning_rate=-1e-4)  # gt=0.0

        with pytest.raises(ValidationError):
            QLoRAConfig(max_seq_length=4096)  # le=2048

        with pytest.raises(ValidationError):
            QLoRAConfig(extra_param="not_allowed")  # extra='forbid'

    def test_to_peft_config(self) -> None:
        cfg = QLoRAConfig(lora_r=32, lora_alpha=64)
        peft_cfg = cfg.to_peft_config()
        assert peft_cfg.r == 32
        assert peft_cfg.lora_alpha == 64
        assert peft_cfg.target_modules == {
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        }

    def test_to_bnb_config(self) -> None:
        cfg = QLoRAConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4")
        bnb_cfg = cfg.to_bnb_config()
        assert bnb_cfg.load_in_4bit is True
        assert bnb_cfg.bnb_4bit_quant_type == "nf4"
        assert bnb_cfg.bnb_4bit_use_double_quant is True


class TestLossMaskingAndDataset:
    """Tests for ChatML prompt formatting, loss masking, and batch collation."""

    def test_format_chatml_messages(self) -> None:
        messages = [
            {"role": "system", "content": "You are a test assistant."},
            {"role": "user", "content": "Diagnose this code."},
            {"role": "assistant", "content": '{"bug_description": "None"}'},
        ]
        prompt, completion = format_chatml_messages(messages)

        assert "<|im_start|>system\nYou are a test assistant.<|im_end|>" in prompt
        assert "<|im_start|>user\nDiagnose this code.<|im_end|>" in prompt
        assert prompt.endswith("<|im_start|>assistant\n")
        assert completion == '{"bug_description": "None"}<|im_end|>\n'

    def test_loss_masking_labels(self, qwen_tokenizer: AutoTokenizer) -> None:
        generator = DatasetGenerator(seed=42)
        samples = generator.generate_all_pairs(total_target=4)
        assert len(samples) == 4

        dataset = InstructionDataset(
            samples=samples,
            tokenizer=qwen_tokenizer,
            max_seq_length=2048,
            mask_prompt_loss=True,
        )

        assert len(dataset) == 4

        for item in dataset:
            input_ids = item["input_ids"]
            labels = item["labels"]
            attention_mask = item["attention_mask"]

            assert len(input_ids) == len(labels) == len(attention_mask)
            assert all(m == 1 for m in attention_mask)

            # Find the first index where label != -100
            prompt_end_idx = next(i for i, lbl in enumerate(labels) if lbl != -100)

            # All tokens before prompt_end_idx must be masked (-100)
            assert all(lbl == -100 for lbl in labels[:prompt_end_idx])

            # All tokens from prompt_end_idx onward must match input_ids
            assert labels[prompt_end_idx:] == input_ids[prompt_end_idx:]

            # The completion tokens should decode to valid JSON + <|im_end|>
            completion_tokens = input_ids[prompt_end_idx:]
            decoded_completion = qwen_tokenizer.decode(completion_tokens)
            assert "<|im_end|>" in decoded_completion
            # Clean off the special token and verify JSON parse
            json_str = decoded_completion.replace("<|im_end|>", "").strip()
            parsed_json = json.loads(json_str)
            assert isinstance(parsed_json, dict)

    def test_dataset_without_loss_masking(self, qwen_tokenizer: AutoTokenizer) -> None:
        generator = DatasetGenerator(seed=42)
        samples = generator.generate_all_pairs(total_target=2)

        dataset = InstructionDataset(
            samples=samples,
            tokenizer=qwen_tokenizer,
            max_seq_length=2048,
            mask_prompt_loss=False,
        )

        for item in dataset:
            # When mask_prompt_loss is False, labels equal input_ids throughout
            assert item["labels"] == item["input_ids"]

    def test_collate_instruction_batch(self, qwen_tokenizer: AutoTokenizer) -> None:
        generator = DatasetGenerator(seed=42)
        samples = generator.generate_all_pairs(total_target=3)
        dataset = InstructionDataset(samples=samples, tokenizer=qwen_tokenizer)

        batch_items = [dataset[0], dataset[1], dataset[2]]
        collated = collate_instruction_batch(batch_items, pad_token_id=0)

        assert "input_ids" in collated
        assert "labels" in collated
        assert "attention_mask" in collated

        input_ids = collated["input_ids"]
        labels = collated["labels"]
        attention_mask = collated["attention_mask"]

        assert input_ids.shape[0] == 3
        assert labels.shape == input_ids.shape
        assert attention_mask.shape == input_ids.shape

        max_len = input_ids.shape[1]
        for i, original_item in enumerate(batch_items):
            orig_len = len(original_item["input_ids"])
            # Verified original tokens preserved
            assert input_ids[i, :orig_len].tolist() == original_item["input_ids"]
            # Verified padding is masked with -100 in labels and 0 in attention_mask
            if orig_len < max_len:
                assert (labels[i, orig_len:] == -100).all()
                assert (attention_mask[i, orig_len:] == 0).all()
                assert (input_ids[i, orig_len:] == 0).all()


class TestQLoRASmokeTraining:
    """Tests for end-to-end QLoRA smoke training and checkpoint reload."""

    def test_smoke_test_convergence_and_checkpoint(self, tmp_path: Path) -> None:
        smoke_out = tmp_path / "smoke_adapter"

        res = run_smoke_test(output_dir=smoke_out)

        metrics = res["metrics"]
        checkpoint = res["checkpoint"]

        # 1. Verify metrics and loss reduction
        assert "initial_val_loss" in metrics
        assert "final_val_loss" in metrics
        assert "loss_reduction" in metrics
        assert metrics["loss_reduction"] > 0, (
            f"Expected positive loss reduction, got {metrics['loss_reduction']} "
            f"(Initial: {metrics['initial_val_loss']}, Final: {metrics['final_val_loss']})"
        )
        assert metrics["schema_parse_rate"] == 1.0
        assert metrics["total_steps"] > 0
        assert metrics["duration_sec"] > 0.0

        # 2. Verify checkpoint verification result
        assert checkpoint["is_valid"] is True
        assert checkpoint["rank"] == 32
        assert checkpoint["alpha"] == 64
        assert checkpoint["has_safetensors"] is True

        # 3. Verify files on disk
        assert (smoke_out / "adapter_model.safetensors").is_file()
        assert (smoke_out / "adapter_config.json").is_file()
        assert (smoke_out / "training_config.json").is_file()
        assert (smoke_out / "training_metrics.json").is_file()

    def test_verify_checkpoint_helper(self, tmp_path: Path) -> None:
        # Non-existent directory
        with pytest.raises(FileNotFoundError):
            verify_checkpoint(tmp_path / "nonexistent")

        # Missing weights file
        test_dir = tmp_path / "corrupt_ckpt"
        test_dir.mkdir(parents=True)
        (test_dir / "adapter_config.json").write_text(json.dumps({"r": 32, "lora_alpha": 64}))

        with pytest.raises(FileNotFoundError, match="Missing adapter_model weights file"):
            verify_checkpoint(test_dir)

    def test_reload_checkpoint_into_model(
        self, tmp_path: Path, qwen_tokenizer: AutoTokenizer
    ) -> None:
        smoke_out = tmp_path / "reload_test_adapter"
        run_smoke_test(output_dir=smoke_out)

        # Build base toy model with identical architecture as used in smoke test
        base_model = build_toy_base_model(vocab_size=len(qwen_tokenizer))

        # Reload adapter onto base model via PEFT
        peft_model = PeftModel.from_pretrained(base_model, str(smoke_out))
        assert peft_model is not None

        # Verify forward pass through reloaded adapter
        dummy_input = torch.tensor([[100, 200, 300]], dtype=torch.long)
        with torch.no_grad():
            outputs = peft_model(dummy_input)
            assert outputs.logits is not None
            assert outputs.logits.shape == (1, 3, len(qwen_tokenizer))
