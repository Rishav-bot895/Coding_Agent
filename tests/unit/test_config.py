"""Unit tests for localdev configuration and token estimation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from localdev.config import LocaldevConfig, estimate_tokens, get_default_config
from localdev.constants import (
    APPLICATION_SAFETY_MARGIN_TOKENS,
    CONTEXT_WINDOW_TOKENS,
    FALLBACK_MODEL,
    OUTPUT_BUDGET_TOKENS,
    PRIMARY_MODEL,
    PROMPT_BUDGET_TOKENS,
)


def test_default_config_values() -> None:
    """Verify default configuration parameters match frozen project constants."""
    cfg = LocaldevConfig()
    assert cfg.context_window_tokens == CONTEXT_WINDOW_TOKENS
    assert cfg.prompt_budget_tokens == PROMPT_BUDGET_TOKENS
    assert cfg.output_budget_tokens == OUTPUT_BUDGET_TOKENS
    assert cfg.application_safety_margin_tokens == APPLICATION_SAFETY_MARGIN_TOKENS
    assert cfg.primary_model == PRIMARY_MODEL
    assert cfg.fallback_model == FALLBACK_MODEL
    assert cfg.active_model == PRIMARY_MODEL
    assert cfg.keep_alive_seconds == 0
    assert cfg.timeout_seconds == 10.0
    assert cfg.output_byte_cap == 512 * 1024
    assert cfg.max_source_size_bytes == 256 * 1024


def test_token_budget_partition_invariant() -> None:
    """Verify that token budget partition must not exceed context window."""
    # Exact sum passes
    valid_cfg = LocaldevConfig(
        context_window_tokens=2048,
        prompt_budget_tokens=1200,
        output_budget_tokens=600,
        application_safety_margin_tokens=248,
    )
    assert valid_cfg.prompt_budget_tokens + valid_cfg.output_budget_tokens + valid_cfg.application_safety_margin_tokens == 2048

    # Over budget raises ValidationError
    with pytest.raises(ValidationError) as exc_info:
        LocaldevConfig(
            context_window_tokens=2048,
            prompt_budget_tokens=1500,
            output_budget_tokens=600,
            application_safety_margin_tokens=248,
        )
    assert "Token budget partition exceeds context window" in str(exc_info.value)


def test_config_validation_rejections() -> None:
    """Assert rejection of negative timeouts, invalid byte caps, or unknown fields."""
    with pytest.raises(ValidationError):
        LocaldevConfig(timeout_seconds=-1.0)

    with pytest.raises(ValidationError):
        LocaldevConfig(output_byte_cap=0)

    with pytest.raises(ValidationError):
        LocaldevConfig(unknown_field="unsupported")  # type: ignore[call-arg]


def test_get_default_config_factory() -> None:
    """Verify get_default_config selects the requested model tier."""
    primary_cfg = get_default_config("primary")
    assert primary_cfg.active_model == PRIMARY_MODEL

    fallback_cfg = get_default_config("fallback")
    assert fallback_cfg.active_model == FALLBACK_MODEL


def test_estimate_tokens_heuristic() -> None:
    """Verify heuristic token estimation behavior and scaling."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1

    sample_code = "def calculate_sum(numbers: list[int]) -> int:\n    return sum(numbers)\n"
    est = estimate_tokens(sample_code)
    assert est > 0
    # ~69 characters at ~3.5 chars/token * 1.15 cushion should be ~23 tokens
    assert 15 <= est <= 35

