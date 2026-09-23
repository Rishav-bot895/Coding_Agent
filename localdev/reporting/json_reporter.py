"""Machine-readable JSON output reporter for localdev.

Serializes command execution results into versioned RFC 8259 JSON envelopes
(schema_version: "1.0") adhering to localdev.schemas.JsonEnvelope. Preserves raw
underlying data (stdout, stderr, code text, control characters) verbatim without
lossy terminal display sanitization.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from localdev.schemas import JsonEnvelope


def create_json_envelope(
    command: str,
    success: bool,
    target_path: str | None = None,
    data: Any = None,
    errors: list[str] | None = None,
    limitations: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> JsonEnvelope[Any]:
    """Construct a strongly typed JsonEnvelope conforming to schema version '1.0'.

    Args:
        command: Name of CLI command executing.
        success: True if command achieved its objectives.
        target_path: Explicit target source path.
        data: Command-specific structured payload.
        errors: List of error messages.
        limitations: List of explicit technical limitations or abstentions.
        metadata: Command execution and environment metadata.

    Returns:
        Validated JsonEnvelope instance.
    """
    return JsonEnvelope[Any](
        schema_version="1.0",
        command=command,
        success=success,
        target_path=target_path,
        data=data,
        errors=errors or [],
        limitations=limitations or [],
        metadata=metadata or {},
    )


def render_json_envelope(envelope: JsonEnvelope[Any]) -> str:
    """Serialize a JsonEnvelope to an RFC 8259 JSON string preserving raw data verbatim.

    Args:
        envelope: Validated JsonEnvelope instance.

    Returns:
        Formatted RFC 8259 JSON string with 2-space indentation.
    """
    # Use Pydantic's model_dump(mode="json") to convert nested models/enums,
    # then json.dumps with ensure_ascii=False to preserve raw unicode text.
    dumped = envelope.model_dump(mode="json")
    return json.dumps(dumped, indent=2, ensure_ascii=False)


def write_json_envelope(
    envelope: JsonEnvelope[Any],
    stream: TextIO | None = None,
) -> None:
    """Serialize and write a JsonEnvelope to an output stream.

    Args:
        envelope: Validated JsonEnvelope instance.
        stream: Target output text stream (defaults to sys.stdout).
    """
    target_stream = stream if stream is not None else sys.stdout
    rendered = render_json_envelope(envelope)
    target_stream.write(rendered + "\n")
    target_stream.flush()

