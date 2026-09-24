"""Runtime environment sanitization and allowlist enforcement.

In accordance with the Runtime and Isolation Contract (Section 3.1):
Child processes inherit only essential operating system environment variables:
SYSTEMROOT, SYSTEMDRIVE, PATH, PATHEXT, TEMP, TMP, COMSPEC, USERNAME.
All other environment variables (including PYTHONPATH, PYTHONHOME, etc.) are stripped.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from localdev.constants import ENV_ALLOWLIST


def build_clean_environment(
    env_overrides: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construct an isolated environment dictionary inheriting only allowlisted variables.

    Args:
        env_overrides: Optional key-value pairs to override or append.
            Must not contain any keys starting with 'PYTHON' to prevent host contamination.
        base_env: Source environment mapping to filter. Defaults to os.environ.

    Returns:
        A new sanitized dictionary containing only allowlisted and permitted override variables,
        with allowlisted keys normalized to uppercase.

    Raises:
        ValueError: If any key in env_overrides begins with 'PYTHON' (case-insensitive).
    """
    source = os.environ if base_env is None else base_env
    allowlist_map = {name.upper(): name for name in ENV_ALLOWLIST}

    clean_env: dict[str, str] = {}
    for key, value in source.items():
        key_upper = key.upper()
        if key_upper in allowlist_map:
            clean_env[allowlist_map[key_upper]] = value

    if env_overrides:
        for k, v in env_overrides.items():
            if k.upper().startswith("PYTHON"):
                raise ValueError(
                    f"Environment override '{k}' is prohibited: PYTHON* variables cannot be injected."
                )
            clean_env[k] = v

    return clean_env


def is_allowed_env_var(name: str) -> bool:
    """Return True if an environment variable name is part of the frozen allowlist."""
    return name.upper() in {var.upper() for var in ENV_ALLOWLIST}
