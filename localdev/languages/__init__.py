"""Language adapter architecture and registry for localdev."""

from __future__ import annotations

from localdev.languages.base import (
    AdapterRegistry,
    LanguageAdapter,
    detect_adapter,
    get_adapter,
    get_default_registry,
    register_adapter,
)
from localdev.languages.detector import (
    detect_target_language,
    is_binary_target,
    parse_shebang,
)
from localdev.languages.python.adapter import PythonAdapter

__all__ = [
    "AdapterRegistry",
    "LanguageAdapter",
    "PythonAdapter",
    "detect_adapter",
    "detect_target_language",
    "get_adapter",
    "get_default_registry",
    "is_binary_target",
    "parse_shebang",
    "register_adapter",
]

