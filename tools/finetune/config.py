"""Configuration dataclasses and Pydantic models for QLoRA fine-tuning (P13-T2).

Defines:
- QLoRAConfig: Quantization, LoRA rank/alpha/targets, and training hyperparameters.
- Loss masking invariants (prompt labels = -100).
- Hardware VRAM budget assertions (< 8-12 GB consumer GPU baseline).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_OUTPUT_DIR: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent / "models" / "finetune" / "qlora_adapter"
)
DEFAULT_DATASET_DIR: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent / "datasets" / "finetune"
)


class QLoRAConfig(BaseModel):
    """Production configuration for QLoRA instruction tuning of Qwen2.5-Coder."""

    model_config = ConfigDict(extra="forbid")

    # Base Model & Tokenizer
    model_id: str = Field(
        default="Qwen/Qwen2.5-Coder-3B-Instruct",
        description="Hugging Face model ID or local directory for base model.",
    )
    fallback_model_id: str = Field(
        default="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        description="Fallback base model ID for lower-memory environments.",
    )
    tokenizer_id: str = Field(
        default="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        description="Tokenizer ID sharing the unified Qwen2.5 vocabulary (151,665 tokens).",
    )
    device: str = Field(
        default="auto",
        description="Target compute device: 'auto', 'cuda', or 'cpu'.",
    )
    torch_dtype: str = Field(
        default="bfloat16",
        description="Computation dtype for activations and gradients: bfloat16, float16, or float32.",
    )

    # 4-Bit NormalFloat Quantization (BitsAndBytes)
    load_in_4bit: bool = Field(
        default=True,
        description="Enable 4-bit NormalFloat base model quantization.",
    )
    bnb_4bit_quant_type: str = Field(
        default="nf4",
        description="Quantization data type: 'nf4' (NormalFloat4) or 'fp4'.",
    )
    bnb_4bit_use_double_quant: bool = Field(
        default=True,
        description="Apply double quantization (quantizing quantization constants) to save 0.4 bits/param.",
    )
    bnb_4bit_compute_dtype: str = Field(
        default="bfloat16",
        description="Compute precision for dequantized 4-bit matrix multiplication.",
    )

    # LoRA Adapter Configuration (PEFT)
    lora_r: int = Field(
        default=32,
        ge=1,
        le=128,
        description="LoRA rank dimension r.",
    )
    lora_alpha: int = Field(
        default=64,
        ge=1,
        le=256,
        description="LoRA scaling factor alpha (alpha / r = 2.0).",
    )
    lora_dropout: float = Field(
        default=0.05,
        ge=0.0,
        le=0.5,
        description="LoRA dropout probability.",
    )
    target_modules: list[str] = Field(
        default_factory=lambda: [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        description="Linear projection layers targeted by LoRA adapter weights.",
    )
    bias: str = Field(
        default="none",
        description="Bias parameter training mode ('none', 'all', or 'lora_only').",
    )

    # Training Hyperparameters
    learning_rate: float = Field(
        default=2e-4,
        gt=0.0,
        le=1e-2,
        description="Peak learning rate for cosine scheduler.",
    )
    lr_scheduler_type: str = Field(
        default="cosine",
        description="Learning rate scheduler strategy: 'cosine', 'linear', or 'constant_with_warmup'.",
    )
    warmup_ratio: float = Field(
        default=0.03,
        ge=0.0,
        le=0.2,
        description="Fraction of total training steps allocated to linear learning rate warm-up.",
    )
    per_device_train_batch_size: int = Field(
        default=2,
        ge=1,
        description="Per-device physical micro-batch size.",
    )
    gradient_accumulation_steps: int = Field(
        default=8,
        ge=1,
        description="Gradient accumulation steps to reach effective batch size (e.g., 2 * 8 = 16).",
    )
    num_train_epochs: float = Field(
        default=3.0,
        gt=0.0,
        description="Total training epochs over the dataset.",
    )
    weight_decay: float = Field(
        default=0.01,
        ge=0.0,
        description="Decoupled weight decay parameter.",
    )
    max_grad_norm: float = Field(
        default=1.0,
        gt=0.0,
        description="Maximum gradient norm threshold for gradient clipping.",
    )
    gradient_checkpointing: bool = Field(
        default=True,
        description="Recompute activations during backward pass to fit within consumer VRAM (< 8-12 GB).",
    )

    # Token Budgets & Loss Masking
    max_seq_length: int = Field(
        default=2048,
        le=2048,
        description="Hard maximum sequence length matching the 2,048-token context window.",
    )
    max_prompt_length: int = Field(
        default=1200,
        le=1200,
        description="Hard upper ceiling on prompt token count.",
    )
    max_completion_length: int = Field(
        default=600,
        le=600,
        description="Hard upper ceiling on target completion token count.",
    )
    mask_prompt_loss: bool = Field(
        default=True,
        description="Mask prompt tokens (labels = -100) so loss is computed exclusively on JSON completions.",
    )

    # File Paths & Checkpointing
    train_file: Path = Field(
        default=DEFAULT_DATASET_DIR / "train.jsonl",
        description="Path to training jsonl split.",
    )
    val_file: Path = Field(
        default=DEFAULT_DATASET_DIR / "val.jsonl",
        description="Path to validation jsonl split.",
    )
    test_file: Path = Field(
        default=DEFAULT_DATASET_DIR / "test.jsonl",
        description="Path to test jsonl split.",
    )
    output_dir: Path = Field(
        default=DEFAULT_OUTPUT_DIR,
        description="Directory where trained LoRA adapter weights and logs are saved.",
    )
    logging_steps: int = Field(default=10, ge=1, description="Interval for logging training metrics.")
    eval_steps: int = Field(default=50, ge=1, description="Interval for evaluating validation split.")
    save_steps: int = Field(default=100, ge=1, description="Interval for saving adapter checkpoints.")
    save_total_limit: int = Field(default=3, ge=1, description="Maximum number of checkpoints to retain.")
    seed: int = Field(default=42, description="Random seed for deterministic initialization.")

    @property
    def effective_batch_size(self) -> int:
        """Total effective batch size across gradient accumulation steps."""
        return self.per_device_train_batch_size * self.gradient_accumulation_steps

    def to_peft_config(self) -> Any:
        """Instantiate a peft.LoraConfig from this configuration."""
        from peft import LoraConfig, TaskType  # type: ignore[import-untyped]

        return LoraConfig(
            r=self.lora_r,
            lora_alpha=self.lora_alpha,
            lora_dropout=self.lora_dropout,
            target_modules=self.target_modules,
            bias=self.bias,
            task_type=TaskType.CAUSAL_LM,
        )

    def to_bnb_config(self) -> Any:
        """Instantiate a transformers.BitsAndBytesConfig if 4-bit is enabled."""
        import torch
        from transformers import BitsAndBytesConfig  # type: ignore[import-untyped]

        compute_dtype = getattr(torch, self.bnb_4bit_compute_dtype, torch.float16)
        return BitsAndBytesConfig(
            load_in_4bit=self.load_in_4bit,
            bnb_4bit_quant_type=self.bnb_4bit_quant_type,
            bnb_4bit_use_double_quant=self.bnb_4bit_use_double_quant,
            bnb_4bit_compute_dtype=compute_dtype,
        )

    def save_json(self, path: Path) -> None:
        """Serialize configuration to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path) -> QLoRAConfig:
        """Deserialize configuration from a JSON file."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

