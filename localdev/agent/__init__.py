"""Agent coordination, permission verification, and session management for localdev."""

from __future__ import annotations

from localdev.agent.orchestrator import Orchestrator
from localdev.agent.permissions import (
    assert_writable_target,
    validate_target,
    verify_target_hash,
)
from localdev.agent.session import (
    Session,
    SessionManifest,
    generate_session_id,
    get_path_volume,
    is_same_volume,
    resolve_same_volume_staging_dir,
    safely_delete_directory,
    verify_session_ownership_marker,
)

__all__ = [
    "Orchestrator",
    "Session",
    "SessionManifest",
    "assert_writable_target",
    "generate_session_id",
    "get_path_volume",
    "is_same_volume",
    "resolve_same_volume_staging_dir",
    "safely_delete_directory",
    "validate_target",
    "verify_session_ownership_marker",
    "verify_target_hash",
]
