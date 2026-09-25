"""Inference client interface and base abstractions for local SLM execution.

Enforces:
- Abstract client interface returning validated Pydantic model instances.
- Structured output constraints using JSON Schema definitions.
- Non-execution invariant: no model output is ever treated as executable instructions,
  shell commands, or direct file mutations.
- Bounded token budgeting metadata capture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from localdev.schemas import InferenceMetadata

T = TypeVar("T", bound=BaseModel)


class BaseInferenceClient(ABC):
    """Abstract base class defining the local SLM inference contract."""

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        keep_alive: int | None = None,
        enforce_prompt_budget: bool = True,
    ) -> tuple[T, InferenceMetadata]:
        """Generate structured output validated against a Pydantic schema.

        Args:
            prompt: User/task prompt text containing facts and evidence.
            schema: Pydantic model class defining the expected output JSON Schema.
            system_prompt: Optional system prompt instructions.
            model: Optional model override (defaults to configured active_model).
            keep_alive: Optional keep_alive in seconds (0 = immediate unload).
            enforce_prompt_budget: Whether to enforce prompt_budget_tokens limit.

        Returns:
            Tuple of (validated_pydantic_instance, inference_metadata).

        Raises:
            InferenceError: On network, HTTP, or operational failure.
            SchemaValidationError: If response cannot be parsed into schema.
            PromptBudgetExceededError: If prompt exceeds prompt_budget_tokens.
        """
        ...

    @abstractmethod
    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        model: str | None = None,
        keep_alive: int | None = None,
        enforce_prompt_budget: bool = True,
    ) -> tuple[T, InferenceMetadata]:
        """Generate structured output from a list of chat messages.

        Args:
            messages: List of chat message dictionaries with 'role' and 'content'.
            schema: Pydantic model class defining expected output JSON Schema.
            model: Optional model override (defaults to configured active_model).
            keep_alive: Optional keep_alive in seconds (0 = immediate unload).
            enforce_prompt_budget: Whether to enforce prompt_budget_tokens limit.

        Returns:
            Tuple of (validated_pydantic_instance, inference_metadata).

        Raises:
            InferenceError: On network, HTTP, or operational failure.
            SchemaValidationError: If response cannot be parsed into schema.
            PromptBudgetExceededError: If prompt exceeds prompt_budget_tokens.
        """
        ...

    @abstractmethod
    def unload_model(self, model: str | None = None) -> bool:
        """Explicitly unload the specified model from memory (keep_alive: 0).

        Args:
            model: Model identifier to unload (defaults to active_model).

        Returns:
            True if model was successfully signaled for unload.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the local inference service is reachable and responsive."""
        ...

