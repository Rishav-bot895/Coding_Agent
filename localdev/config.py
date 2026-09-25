"""Configuration management and token estimation for localdev.

Provides strongly typed configuration models, token budgeting rules, and heuristic
token estimation for offline local SLM inference on Windows 11 x64.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from localdev.constants import (
    APPLICATION_SAFETY_MARGIN_TOKENS,
    CHARS_PER_TOKEN_HEURISTIC,
    CONTEXT_WINDOW_TOKENS,
    DEFAULT_INFERENCE_TIMEOUT_SECONDS,
    DEFAULT_KEEP_ALIVE_SECONDS,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OUTPUT_BYTE_CAP,
    DEFAULT_TIMEOUT_SECONDS,
    FALLBACK_MODEL,
    MAX_SOURCE_SIZE_BYTES,
    OUTPUT_BUDGET_TOKENS,
    PRIMARY_MODEL,
    PROCESS_MEMORY_SAMPLE_INTERVAL_MS,
    PROMPT_BUDGET_TOKENS,
)


class LocaldevConfig(BaseModel):
    """Global configuration settings for localdev execution and inference."""

    model_config = ConfigDict(extra="forbid")

    # Ollama inference settings
    ollama_url: str = Field(
        default=DEFAULT_OLLAMA_URL,
        description="Base URL for local Ollama HTTP service.",
    )
    primary_model: str = Field(
        default=PRIMARY_MODEL,
        description="Primary local SLM model identifier.",
    )
    fallback_model: str = Field(
        default=FALLBACK_MODEL,
        description="Low-memory fallback SLM model identifier for 8 GB RAM budget.",
    )
    active_model: str = Field(
        default=PRIMARY_MODEL,
        description="Model currently selected for inference.",
    )

    # Token budgets (hard partition: prompt + output + margin == context_window)
    context_window_tokens: int = Field(
        default=CONTEXT_WINDOW_TOKENS,
        ge=512,
        le=8192,
        description="Hard context window ceiling passed to Ollama (num_ctx).",
    )
    prompt_budget_tokens: int = Field(
        default=PROMPT_BUDGET_TOKENS,
        ge=256,
        description="Maximum tokens allowed for system prompt, evidence, and code.",
    )
    output_budget_tokens: int = Field(
        default=OUTPUT_BUDGET_TOKENS,
        ge=128,
        description="Maximum tokens allowed for model output (num_predict).",
    )
    application_safety_margin_tokens: int = Field(
        default=APPLICATION_SAFETY_MARGIN_TOKENS,
        ge=64,
        description="Safety margin for prompt framing, JSON schema, and tokenization uncertainty.",
    )

    # Subprocess execution and operational limits (non-sandbox)
    timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        gt=0.0,
        description="Wall-clock timeout in seconds for target subprocess execution.",
    )
    output_byte_cap: int = Field(
        default=DEFAULT_OUTPUT_BYTE_CAP,
        gt=0,
        description="Combined stdout/stderr output cap in bytes before truncation.",
    )
    max_source_size_bytes: int = Field(
        default=MAX_SOURCE_SIZE_BYTES,
        gt=0,
        description="Maximum allowed source file size in bytes for target files.",
    )
    process_memory_sample_interval_ms: int = Field(
        default=PROCESS_MEMORY_SAMPLE_INTERVAL_MS,
        gt=0,
        description="Sampling interval in ms for process tree RSS tracking.",
    )

    # Model lifecycle policy
    keep_alive_seconds: int = Field(
        default=DEFAULT_KEEP_ALIVE_SECONDS,
        ge=0,
        description="Ollama keep_alive parameter in seconds. 0 indicates immediate unload.",
    )
    inference_timeout_seconds: float = Field(
        default=DEFAULT_INFERENCE_TIMEOUT_SECONDS,
        gt=0.0,
        description="Timeout in seconds for local SLM HTTP inference requests.",
    )

    # Operational & debugging flags
    fail_on_job_failure: bool = Field(
        default=False,
        description="If True, abort execution when Windows Job Object assignment fails.",
    )
    keep_session: bool = Field(
        default=False,
        description="If True, retain temporary session directory after run for debugging.",
    )
    chars_per_token_heuristic: float = Field(
        default=CHARS_PER_TOKEN_HEURISTIC,
        gt=0.0,
        description="Fallback character-to-token ratio for conservative token estimation.",
    )

    @model_validator(mode="after")
    def validate_token_budget_partition(self) -> LocaldevConfig:
        """Enforce prompt_budget + output_budget + safety_margin <= context_window."""
        total = (
            self.prompt_budget_tokens
            + self.output_budget_tokens
            + self.application_safety_margin_tokens
        )
        if total > self.context_window_tokens:
            raise ValueError(
                f"Token budget partition exceeds context window: "
                f"{self.prompt_budget_tokens} (prompt) + {self.output_budget_tokens} (output) + "
                f"{self.application_safety_margin_tokens} (margin) = {total} > {self.context_window_tokens}"
            )
        return self


def estimate_tokens(
    text: str,
    chars_per_token: float = CHARS_PER_TOKEN_HEURISTIC,
    safety_cushion: float = 1.15,
) -> int:
    """Heuristically estimate token count using conservative character ratios.

    Uses a conservative fallback of ~3.5 characters per token with an additional 15%
    safety cushion to guard against token budget overruns when an exact tokenizer
    is unavailable.
    """
    if not text:
        return 0
    raw_estimate = len(text) / chars_per_token
    return max(1, math.ceil(raw_estimate * safety_cushion))


def get_default_config(
    model_tier: Literal["primary", "fallback"] = "primary",
) -> LocaldevConfig:
    """Return default validated LocaldevConfig for the requested model tier."""
    cfg = LocaldevConfig()
    if model_tier == "fallback":
        cfg.active_model = cfg.fallback_model
    else:
        cfg.active_model = cfg.primary_model
    return cfg
