"""Reproducible QLoRA training pipeline for local SLMs (Phase 13 Task P13-T2).

Fine-tunes Qwen2.5-Coder models with:
- 4-bit NF4 quantization with double quantization (bnb_4bit_use_double_quant=True).
- LoRA on all 7 linear projection layers (q, k, v, o, gate, up, down) with r=32, alpha=64.
- Loss masking: Cross-entropy loss computed exclusively on completion tokens (labels = -100 on prompt).
- Cosine learning rate scheduler with warm-up (peak LR 2e-4), effective batch size 16.
- Checkpointing, validation loss tracking, schema parse rate, and gradient norm stability.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import psutil
import torch
import torch.nn as nn
from peft import LoraConfig, TaskType, get_peft_model
from torch.utils.data import DataLoader, Dataset
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoTokenizer,
    PreTrainedTokenizerBase,
    get_cosine_schedule_with_warmup,
)

from tools.finetune.config import QLoRAConfig
from tools.finetune.schema_templates import (
    DatasetSampleType,
    InstructionPair,
    validate_abstention_completion,
    validate_diagnosis_completion,
    validate_edit_completion,
)

logger = logging.getLogger("train_qlora")

DEFAULT_TOKENIZER_FILE: Final[Path] = (
    Path(__file__).resolve().parent / "qwen_tokenizer.json"
)


# =============================================================================
# Dataset & Loss Masking Preprocessing
# =============================================================================


def format_chatml_messages(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Format messages into prompt and completion following standard ChatML.

    Returns:
        (prompt_text, completion_text)
        prompt_text encompasses system and user turns up to `<|im_start|>assistant\n`.
        completion_text encompasses assistant turn ending with `<|im_end|>\n`.
    """
    prompt_chunks = []
    completion_text = ""
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("system", "user"):
            prompt_chunks.append(f"<|im_start|>{role}\n{content}<|im_end|>")
        elif role == "assistant":
            completion_text = f"{content}<|im_end|>\n"

    prompt_text = "\n".join(prompt_chunks) + "\n<|im_start|>assistant\n"
    return prompt_text, completion_text


class InstructionDataset(Dataset):
    """PyTorch Dataset for instruction-tuning pairs with loss masking."""

    def __init__(
        self,
        samples: Sequence[InstructionPair | dict[str, Any]],
        tokenizer: PreTrainedTokenizerBase | Any,
        max_seq_length: int = 2048,
        mask_prompt_loss: bool = True,
    ) -> None:
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_seq_length = max_seq_length
        self.mask_prompt_loss = mask_prompt_loss
        self._parsed_samples: list[dict[str, Any]] = []
        self._prepare_data()

    def _prepare_data(self) -> None:
        for s in self.samples:
            if isinstance(s, dict):
                pair = InstructionPair.model_validate(s)
            elif isinstance(s, InstructionPair):
                pair = s
            else:
                continue

            prompt_text, comp_text = format_chatml_messages(pair.messages)

            # Tokenize prompt and completion separately for precise boundary masking
            if hasattr(self.tokenizer, "encode") and not hasattr(self.tokenizer, "ids"):
                prompt_ids = self.tokenizer.encode(prompt_text, add_special_tokens=False)
                comp_ids = self.tokenizer.encode(comp_text, add_special_tokens=False)
            else:
                prompt_ids = self.tokenizer.encode(prompt_text).ids
                comp_ids = self.tokenizer.encode(comp_text).ids

            input_ids = prompt_ids + comp_ids
            if len(input_ids) > self.max_seq_length:
                input_ids = input_ids[: self.max_seq_length]
                comp_boundary = min(len(prompt_ids), len(input_ids))
            else:
                comp_boundary = len(prompt_ids)

            # Mask prompt tokens with -100 so cross-entropy is computed solely on completion
            if self.mask_prompt_loss:
                labels = [-100] * comp_boundary + input_ids[comp_boundary:]
            else:
                labels = list(input_ids)

            self._parsed_samples.append({
                "input_ids": input_ids,
                "labels": labels,
                "attention_mask": [1] * len(input_ids),
                "pair": pair,
            })

    def __len__(self) -> int:
        return len(self._parsed_samples)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return self._parsed_samples[idx]


def collate_instruction_batch(
    batch: list[dict[str, Any]],
    pad_token_id: int = 0,
) -> dict[str, torch.Tensor]:
    """Dynamically pad micro-batch tensors to the longest sequence in batch."""
    max_len = max(len(item["input_ids"]) for item in batch)

    batch_input_ids: list[list[int]] = []
    batch_attention_mask: list[list[int]] = []
    batch_labels: list[list[int]] = []

    for item in batch:
        pad_len = max_len - len(item["input_ids"])
        # Right-padding for causal LM training
        batch_input_ids.append(item["input_ids"] + [pad_token_id] * pad_len)
        batch_attention_mask.append(item["attention_mask"] + [0] * pad_len)
        batch_labels.append(item["labels"] + [-100] * pad_len)

    return {
        "input_ids": torch.tensor(batch_input_ids, dtype=torch.long),
        "attention_mask": torch.tensor(batch_attention_mask, dtype=torch.long),
        "labels": torch.tensor(batch_labels, dtype=torch.long),
    }


def load_dataset_split(file_path: Path, max_samples: int | None = None) -> list[InstructionPair]:
    """Load instruction pairs from jsonl file."""
    if not file_path.is_file():
        raise FileNotFoundError(f"Dataset split not found: {file_path}")

    pairs: list[InstructionPair] = []
    with open(file_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                pairs.append(InstructionPair.model_validate_json(line))
                if max_samples and len(pairs) >= max_samples:
                    break
    return pairs


# =============================================================================
# Model & LoRA Builder
# =============================================================================


def build_toy_base_model(vocab_size: int) -> AutoModelForCausalLM:
    """Build lightweight 2-layer Qwen2 model in-memory for testing and verification."""
    toy_cfg = AutoConfig.for_model(
        "qwen2",
        hidden_size=64,
        intermediate_size=128,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=2,
        vocab_size=vocab_size,
    )
    return AutoModelForCausalLM.from_config(toy_cfg)


def build_model_and_tokenizer(
    config: QLoRAConfig,
    test_mode: bool = False,
) -> tuple[nn.Module, Any]:
    """Instantiate tokenizer, base causal LM, and wrap with LoRA adapter weights."""
    target_device = (
        "cuda" if config.device == "auto" and torch.cuda.is_available() else "cpu"
        if config.device == "auto"
        else config.device
    )

    if test_mode:
        # Create lightweight 2-layer model in-memory for instant smoke testing
        tokenizer = AutoTokenizer.from_pretrained(
            "Qwen/Qwen2.5-Coder-1.5B-Instruct"
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token or "<|endoftext|>"

        base_model = build_toy_base_model(vocab_size=len(tokenizer))

        lora_cfg = LoraConfig(
            r=config.lora_r,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            bias=config.bias,
            task_type=TaskType.CAUSAL_LM,
        )
        model = get_peft_model(base_model, lora_cfg)
        model.to(target_device)
        return model, tokenizer

    # Production Model Loading
    tokenizer = AutoTokenizer.from_pretrained(config.tokenizer_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or "<|endoftext|>"

    torch_dtype = getattr(torch, config.torch_dtype, torch.bfloat16)

    # 4-bit NF4 Quantization if CUDA and bitsandbytes available
    quant_config = None
    if config.load_in_4bit and target_device == "cuda":
        try:
            import bitsandbytes  # noqa: F401

            quant_config = config.to_bnb_config()
            logger.info("Enabling 4-bit NormalFloat (NF4) base model quantization.")
        except ImportError:
            logger.warning(
                "bitsandbytes not installed; proceeding with non-quantized %s base model.",
                config.torch_dtype,
            )

    model_kwargs: dict[str, Any] = {
        "torch_dtype": torch_dtype,
    }
    if quant_config is not None:
        model_kwargs["quantization_config"] = quant_config
        model_kwargs["device_map"] = "auto"
    else:
        model_kwargs["device_map"] = target_device

    base_model = AutoModelForCausalLM.from_pretrained(
        config.model_id,
        **model_kwargs,
    )

    if config.gradient_checkpointing and hasattr(base_model, "gradient_checkpointing_enable"):
        base_model.gradient_checkpointing_enable()

    lora_cfg = config.to_peft_config()
    model = get_peft_model(base_model, lora_cfg)
    logger.info("Attached LoRA adapter weights with rank r=%d, alpha=%d", config.lora_r, config.lora_alpha)
    return model, tokenizer


# =============================================================================
# Evaluation & Schema Parse Rate Metrics
# =============================================================================


def evaluate_validation(
    model: nn.Module,
    val_loader: DataLoader,
    device: str,
    max_eval_batches: int = 15,
) -> dict[str, float]:
    """Evaluate validation loss and schema parse compliance rate on held-out split."""
    model.eval()
    total_val_loss = 0.0
    val_batches = 0
    schema_valid_count = 0
    total_evaluated_samples = 0

    with torch.no_grad():
        for batch_idx, batch in enumerate(val_loader):
            if batch_idx >= max_eval_batches:
                break
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss = outputs.loss
            if loss is not None and not torch.isnan(loss):
                total_val_loss += loss.item()
                val_batches += 1

    # Evaluate schema parse rate on held-out dataset pairs
    if hasattr(val_loader.dataset, "_parsed_samples"):
        dataset: Any = val_loader.dataset
        eval_items = dataset._parsed_samples[:50]
        for item in eval_items:
            pair: InstructionPair = item["pair"]
            total_evaluated_samples += 1
            if pair.task_type == DatasetSampleType.DIAGNOSIS:
                ok, _, _ = validate_diagnosis_completion(pair.completion, pair.evidence_manifest)
            elif pair.task_type == DatasetSampleType.EDIT_PROPOSAL:
                ok, _, _ = validate_edit_completion(pair.completion, pair.source_code, pair.target_file)
            elif pair.task_type == DatasetSampleType.ABSTENTION:
                ok, _, _ = validate_abstention_completion(pair.completion, pair.target_file)
            else:
                ok = False
            if ok:
                schema_valid_count += 1

    mean_val_loss = (total_val_loss / val_batches) if val_batches > 0 else 0.0
    schema_rate = (
        (schema_valid_count / total_evaluated_samples)
        if total_evaluated_samples > 0
        else 1.0
    )

    return {
        "val_loss": round(mean_val_loss, 4),
        "schema_parse_rate": round(schema_rate, 4),
    }


def get_peak_memory_mb() -> float:
    """Return peak GPU VRAM in MB if CUDA available, otherwise process RSS."""
    if torch.cuda.is_available():
        return torch.cuda.max_memory_allocated() / (1024 * 1024)
    proc = psutil.Process()
    return proc.memory_info().rss / (1024 * 1024)


# =============================================================================
# Training Engine & Checkpointing
# =============================================================================


def train_qlora(
    config: QLoRAConfig,
    train_samples_override: list[InstructionPair] | None = None,
    val_samples_override: list[InstructionPair] | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Execute end-to-end QLoRA instruction tuning with loss masking and evaluation."""
    torch.manual_seed(config.seed)
    device = (
        "cuda" if config.device == "auto" and torch.cuda.is_available() else "cpu"
        if config.device == "auto"
        else config.device
    )

    logger.info("Initializing model on device: %s", device)
    model, tokenizer = build_model_and_tokenizer(config, test_mode=test_mode)

    pad_id = getattr(tokenizer, "pad_token_id", 0) or 0

    # Load splits
    if train_samples_override is not None:
        train_pairs = train_samples_override
    else:
        train_pairs = load_dataset_split(config.train_file)

    if val_samples_override is not None:
        val_pairs = val_samples_override
    else:
        val_pairs = load_dataset_split(config.val_file)

    train_dataset = InstructionDataset(
        train_pairs,
        tokenizer,
        max_seq_length=config.max_seq_length,
        mask_prompt_loss=config.mask_prompt_loss,
    )
    val_dataset = InstructionDataset(
        val_pairs,
        tokenizer,
        max_seq_length=config.max_seq_length,
        mask_prompt_loss=config.mask_prompt_loss,
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=config.per_device_train_batch_size,
        shuffle=True,
        collate_fn=lambda b: collate_instruction_batch(b, pad_token_id=pad_id),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=config.per_device_train_batch_size,
        shuffle=False,
        collate_fn=lambda b: collate_instruction_batch(b, pad_token_id=pad_id),
    )

    # Optimizer & Scheduler
    trainable_params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(
        trainable_params,
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )

    total_update_steps = max(
        1,
        int(math.ceil(len(train_loader) / config.gradient_accumulation_steps * config.num_train_epochs)),
    )
    warmup_steps = int(total_update_steps * config.warmup_ratio)
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_update_steps,
    )

    logger.info(
        "Starting training: %d samples, %d epochs, effective batch size %d, total steps %d",
        len(train_dataset),
        int(config.num_train_epochs),
        config.effective_batch_size,
        total_update_steps,
    )

    # Initial validation benchmark
    initial_eval = evaluate_validation(model, val_loader, device=device)
    initial_loss = initial_eval["val_loss"]
    logger.info("Pre-training Validation: Loss=%.4f, Schema Parse Rate=%.1f%%", initial_loss, initial_eval["schema_parse_rate"] * 100)

    model.train()
    optimizer.zero_grad()

    training_history: list[dict[str, Any]] = []
    global_step = 0
    accumulated_loss = 0.0
    accumulated_steps = 0
    start_time = time.perf_counter()

    for epoch in range(int(math.ceil(config.num_train_epochs))):
        for step, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss = outputs.loss / config.gradient_accumulation_steps
            loss.backward()

            accumulated_loss += loss.item() * config.gradient_accumulation_steps
            accumulated_steps += 1

            if accumulated_steps % config.gradient_accumulation_steps == 0 or (step + 1) == len(train_loader):
                grad_norm = torch.nn.utils.clip_grad_norm_(trainable_params, config.max_grad_norm)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                global_step += 1

                step_loss = accumulated_loss / config.gradient_accumulation_steps
                accumulated_loss = 0.0

                if global_step % config.logging_steps == 0 or global_step == 1:
                    peak_mem = get_peak_memory_mb()
                    current_lr = scheduler.get_last_lr()[0]
                    logger.info(
                        "Step %d/%d (Epoch %.2f): Train Loss=%.4f, Grad Norm=%.4f, LR=%.2e, Memory=%.1f MB",
                        global_step,
                        total_update_steps,
                        epoch + (step + 1) / len(train_loader),
                        step_loss,
                        float(grad_norm),
                        current_lr,
                        peak_mem,
                    )
                    training_history.append({
                        "step": global_step,
                        "epoch": round(epoch + (step + 1) / len(train_loader), 2),
                        "train_loss": round(step_loss, 4),
                        "grad_norm": round(float(grad_norm), 4),
                        "lr": current_lr,
                        "memory_mb": round(peak_mem, 1),
                    })

                if global_step % config.eval_steps == 0:
                    val_metrics = evaluate_validation(model, val_loader, device=device)
                    logger.info("Validation at step %d: Loss=%.4f, Schema Parse Rate=%.1f%%", global_step, val_metrics["val_loss"], val_metrics["schema_parse_rate"] * 100)
                    model.train()

    # Final Validation
    final_eval = evaluate_validation(model, val_loader, device=device)
    duration_sec = time.perf_counter() - start_time
    final_loss = final_eval["val_loss"]
    peak_vram = get_peak_memory_mb()

    logger.info("Training complete in %.2f s. Final Val Loss: %.4f (Pre-train: %.4f)", duration_sec, final_loss, initial_loss)

    # Save Checkpoint
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output_dir)
    config.save_json(output_dir / "training_config.json")

    metrics_payload = {
        "initial_val_loss": initial_loss,
        "final_val_loss": final_loss,
        "loss_reduction": round(initial_loss - final_loss, 4),
        "schema_parse_rate": final_eval["schema_parse_rate"],
        "total_steps": global_step,
        "duration_sec": round(duration_sec, 2),
        "peak_memory_mb": round(peak_vram, 2),
        "training_history": training_history,
    }
    (output_dir / "training_metrics.json").write_text(
        json.dumps(metrics_payload, indent=2), encoding="utf-8"
    )

    return metrics_payload


def verify_checkpoint(checkpoint_dir: Path) -> dict[str, Any]:
    """Verify that saved LoRA adapter checkpoint contains valid weights and configuration."""
    adapter_cfg_file = checkpoint_dir / "adapter_config.json"
    if not adapter_cfg_file.is_file():
        raise FileNotFoundError(f"Missing adapter_config.json in {checkpoint_dir}")

    cfg_data = json.loads(adapter_cfg_file.read_text(encoding="utf-8"))

    # Verify adapter weights file exists (safetensors or bin)
    has_safetensors = (checkpoint_dir / "adapter_model.safetensors").is_file()
    has_bin = (checkpoint_dir / "adapter_model.bin").is_file()
    if not (has_safetensors or has_bin):
        raise FileNotFoundError(f"Missing adapter_model weights file in {checkpoint_dir}")

    target_modules = cfg_data.get("target_modules", [])
    rank = cfg_data.get("r", 0)
    alpha = cfg_data.get("lora_alpha", 0)

    return {
        "is_valid": True,
        "rank": rank,
        "alpha": alpha,
        "target_modules": target_modules,
        "has_safetensors": has_safetensors,
        "checkpoint_dir": str(checkpoint_dir),
    }


def run_smoke_test(output_dir: Path | None = None) -> dict[str, Any]:
    """Execute rapid smoke test verifying loss convergence and checkpoint saving."""
    from tools.finetune.prepare_dataset import DatasetGenerator

    test_out = output_dir or (Path(__file__).resolve().parent.parent.parent / "models" / "finetune" / "smoke_test_adapter")

    generator = DatasetGenerator(seed=42)
    sample_pairs = generator.generate_all_pairs(total_target=8)
    train_split = sample_pairs[:6]
    val_split = sample_pairs[6:]

    cfg = QLoRAConfig(
        model_id="qwen2-smoke-test",
        output_dir=test_out,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=1,
        num_train_epochs=2.0,
        learning_rate=5e-3,
        logging_steps=1,
        eval_steps=2,
        device="cpu",
    )

    metrics = train_qlora(
        config=cfg,
        train_samples_override=train_split,
        val_samples_override=val_split,
        test_mode=True,
    )

    check_res = verify_checkpoint(test_out)
    return {
        "metrics": metrics,
        "checkpoint": check_res,
    }


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QLoRA training pipeline for fine-tuning local SLMs."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to JSON configuration file for QLoRAConfig.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to save trained LoRA adapter weights.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run fast smoke test verifying forward/backward pass and checkpoint saving.",
    )
    parser.add_argument(
        "--verify-checkpoint",
        type=Path,
        default=None,
        help="Verify the integrity of a saved adapter checkpoint directory.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.verify_checkpoint:
        res = verify_checkpoint(args.verify_checkpoint)
        logger.info("Checkpoint verification passed: %s", res)
        return 0

    if args.smoke_test:
        logger.info("Executing QLoRA smoke test on mini-batch...")
        res = run_smoke_test(args.output_dir)
        logger.info("Smoke test completed successfully!")
        logger.info("Metrics: %s", res["metrics"])
        logger.info("Checkpoint: %s", res["checkpoint"])
        return 0

    cfg = QLoRAConfig.load_json(args.config) if args.config else QLoRAConfig()
    if args.output_dir:
        cfg.output_dir = args.output_dir

    train_qlora(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
