"""Local Ollama HTTP inference client implementing structured JSON Schema decoding.

Enforces:
- Strictly local offline execution (refusing non-loopback endpoints).
- Separated token budgets:
  - 2,048-token context ceiling passed as options.num_ctx.
  - 600-token output prediction budget passed as options.num_predict.
  - 1,200-token application-enforced prompt budget with 248-token safety margin.
- Real Ollama JSON Schema grammar-constrained decoding via the 'format' parameter.
- Immediate application-side Pydantic validation (rejecting malformed/invalid output).
- Detailed Ollama evaluation usage metrics capture (tokens and durations).
- Model lifecycle management with explicit keep_alive support.
- Non-execution invariant: no model output is ever treated as executable instructions,
  shell commands, or direct file mutations.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Self, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from localdev.config import LocaldevConfig, estimate_tokens, get_default_config
from localdev.errors import (
    InferenceError,
    NonLocalUrlError,
    OllamaConnectionError,
    OllamaContextExceededError,
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    PromptBudgetExceededError,
    SchemaValidationError,
)
from localdev.inference.base import BaseInferenceClient
from localdev.schemas import InferenceMetadata

T = TypeVar("T", bound=BaseModel)

LOCAL_HOSTNAMES: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def validate_local_endpoint(url: str) -> None:
    """Validate that the Ollama endpoint is strictly local loopback.

    Args:
        url: URL string to inspect.

    Raises:
        NonLocalUrlError: If the URL points to an external or non-loopback host.
    """
    if not url or not url.strip():
        raise NonLocalUrlError("Ollama endpoint URL cannot be empty.")

    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError as exc:
        raise NonLocalUrlError(f"Malformed Ollama URL '{url}': {exc}") from exc

    if parts.scheme not in ("http", "https"):
        raise NonLocalUrlError(
            f"Unsupported scheme '{parts.scheme}' in Ollama URL '{url}'. Expected http or https."
        )

    hostname = (parts.hostname or "").lower()
    if hostname not in LOCAL_HOSTNAMES:
        raise NonLocalUrlError(
            f"Refusing non-local endpoint '{url}'. localdev operates strictly offline "
            f"with local Ollama on 127.0.0.1 or localhost."
        )


class OllamaClient(BaseInferenceClient):
    """Local HTTP inference client for Ollama with JSON Schema constrained decoding."""

    def __init__(
        self,
        config: LocaldevConfig | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize the Ollama client.

        Args:
            config: Localdev configuration. Defaults to standard configuration.
            http_client: Optional pre-configured httpx.Client for dependency injection.
        """
        self.config = config or get_default_config()
        validate_local_endpoint(self.config.ollama_url)
        self.timeout = self.config.inference_timeout_seconds

        self._owns_client = http_client is None
        self._http_client = http_client or httpx.Client(
            base_url=self.config.ollama_url,
            timeout=self.timeout,
        )

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
            schema: Pydantic model class defining expected output JSON Schema.
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
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        return self.chat_structured(
            messages,
            schema,
            model=model,
            keep_alive=keep_alive,
            enforce_prompt_budget=enforce_prompt_budget,
        )

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
        target_model = model or self.config.active_model
        eff_keep_alive = keep_alive if keep_alive is not None else self.config.keep_alive_seconds

        # 1. Estimate prompt tokens and optionally enforce application prompt budget
        combined_text = "\n".join(m.get("content", "") for m in messages)
        estimated_tokens = estimate_tokens(
            combined_text,
            chars_per_token=self.config.chars_per_token_heuristic,
        )

        if enforce_prompt_budget and estimated_tokens > self.config.prompt_budget_tokens:
            raise PromptBudgetExceededError(
                f"Prompt estimated at {estimated_tokens} tokens exceeds the prompt budget "
                f"of {self.config.prompt_budget_tokens} tokens.",
                estimated_tokens=estimated_tokens,
                prompt_budget=self.config.prompt_budget_tokens,
            )

        # 2. Extract JSON Schema for grammar-constrained decoding
        format_schema = schema.model_json_schema()

        # 3. Assemble separated token budgets in request options
        options: dict[str, Any] = {
            "num_ctx": self.config.context_window_tokens,
            "num_predict": self.config.output_budget_tokens,
        }

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "format": format_schema,
            "options": options,
            "stream": False,
            "keep_alive": eff_keep_alive,
        }

        # 4. Dispatch HTTP request to local Ollama API
        url = f"{self.config.ollama_url.rstrip('/')}/api/chat"
        try:
            response = self._http_client.post(url, json=payload, timeout=self.timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OllamaConnectionError(
                f"Ollama server is not running or unreachable at {self.config.ollama_url}. "
                f"Ensure Ollama is started with 'ollama serve'."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                f"Ollama request timed out after {self.timeout:.1f} seconds."
            ) from exc
        except httpx.HTTPError as exc:
            raise InferenceError(
                f"HTTP transport error communicating with Ollama: {exc}"
            ) from exc

        # 5. Handle HTTP status codes and API errors
        if response.status_code == 404:
            raise OllamaModelNotFoundError(
                f"Model '{target_model}' not found in Ollama. "
                f"Pull it with 'ollama pull {target_model}'.",
                model_name=target_model,
            )

        if response.status_code == 400:
            err_msg = ""
            try:
                err_msg = response.json().get("error", "")
            except (ValueError, json.JSONDecodeError, httpx.DecodingError):
                err_msg = response.text
            if "context" in err_msg.lower() and "exceed" in err_msg.lower():
                raise OllamaContextExceededError(f"Ollama context window exceeded: {err_msg}")
            if "model" in err_msg.lower() and "not found" in err_msg.lower():
                raise OllamaModelNotFoundError(err_msg, model_name=target_model)
            raise InferenceError(f"Ollama request error (400): {err_msg}", status_code=400)

        if not response.is_success:
            raise InferenceError(
                f"Ollama returned HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

        # 6. Parse response JSON envelope
        try:
            resp_data: dict[str, Any] = response.json()
        except (ValueError, json.JSONDecodeError, httpx.DecodingError) as exc:
            raise SchemaValidationError(
                f"Ollama response is not valid JSON: {exc}",
                raw_payload=response.text,
            ) from exc

        if "error" in resp_data:
            err_text = str(resp_data["error"])
            if "model" in err_text.lower() and "not found" in err_text.lower():
                raise OllamaModelNotFoundError(err_text, model_name=target_model)
            if "context" in err_text.lower() and "exceed" in err_text.lower():
                raise OllamaContextExceededError(err_text)
            raise InferenceError(f"Ollama returned error: {err_text}")

        # Extract content text
        raw_content = ""
        if "message" in resp_data and isinstance(resp_data["message"], dict):
            raw_content = resp_data["message"].get("content", "")
        elif "response" in resp_data:
            raw_content = str(resp_data["response"])

        if not raw_content or not raw_content.strip():
            raise SchemaValidationError(
                "Ollama returned empty response content.",
                raw_payload=raw_content,
            )

        # 7. Strict application-side Pydantic validation
        try:
            validated_record = schema.model_validate_json(raw_content)
        except (ValidationError, ValueError) as exc:
            raise SchemaValidationError(
                f"Model response failed Pydantic validation for {schema.__name__}: {exc}",
                raw_payload=raw_content,
            ) from exc

        # 8. Extract real Ollama evaluation usage metrics
        prompt_eval_count = resp_data.get("prompt_eval_count")
        eval_count = resp_data.get("eval_count")
        prompt_eval_duration = resp_data.get("prompt_eval_duration")
        eval_duration = resp_data.get("eval_duration")

        metadata = InferenceMetadata(
            context_window_tokens=self.config.context_window_tokens,
            prompt_budget_tokens=self.config.prompt_budget_tokens,
            output_budget_tokens=self.config.output_budget_tokens,
            application_safety_margin_tokens=self.config.application_safety_margin_tokens,
            estimated_prompt_tokens=estimated_tokens,
            prompt_eval_count=int(prompt_eval_count) if prompt_eval_count is not None else None,
            eval_count=int(eval_count) if eval_count is not None else None,
            prompt_eval_duration_ms=(
                float(prompt_eval_duration) / 1_000_000.0
                if prompt_eval_duration is not None
                else None
            ),
            eval_duration_ms=(
                float(eval_duration) / 1_000_000.0
                if eval_duration is not None
                else None
            ),
            was_truncated=False,
            omitted_evidence_categories=[],
        )

        return validated_record, metadata

    def unload_model(self, model: str | None = None) -> bool:
        """Explicitly unload the specified model from memory (keep_alive: 0).

        Args:
            model: Model identifier to unload (defaults to configured active_model).

        Returns:
            True if model was successfully signaled for unload.
        """
        target_model = model or self.config.active_model
        url = f"{self.config.ollama_url.rstrip('/')}/api/generate"
        try:
            resp = self._http_client.post(
                url,
                json={"model": target_model, "keep_alive": 0},
                timeout=self.timeout,
            )
            return resp.is_success
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            return False

    def is_available(self) -> bool:
        """Check if the local inference service is reachable and responsive.

        Returns:
            True if Ollama server responds successfully on /api/tags.
        """
        url = f"{self.config.ollama_url.rstrip('/')}/api/tags"
        try:
            resp = self._http_client.get(url, timeout=min(2.0, self.timeout))
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            return False

    def list_models(self) -> list[str]:
        """List model identifiers currently available in the local Ollama instance.

        Returns:
            List of model name strings.
        """
        url = f"{self.config.ollama_url.rstrip('/')}/api/tags"
        try:
            resp = self._http_client.get(url, timeout=self.timeout)
            if resp.is_success:
                data = resp.json()
                models = data.get("models", [])
                return [m.get("name", "") for m in models if isinstance(m, dict) and "name" in m]
            return []
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            return []

    def close(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client:
            self._http_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()
