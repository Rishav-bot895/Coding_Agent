"""Application constants and frozen operational parameters for localdev.

Defines versioning, platform restrictions, resource limits, environment allowlists,
token budgets, complexity vocabularies, and exit semantics for the Windows single-file
offline Python coding agent.
"""

from typing import Final

# -----------------------------------------------------------------------------
# Identity and Versioning
# -----------------------------------------------------------------------------
APP_NAME: Final[str] = "localdev"
APP_VERSION: Final[str] = "0.1.0"
SCHEMA_VERSION: Final[str] = "1.0"

# -----------------------------------------------------------------------------
# Platform & Runtime Constraints
# -----------------------------------------------------------------------------
# Supported platform: Windows 11 x64 only.
SUPPORTED_PLATFORM: Final[str] = "win32"
MIN_PYTHON_VERSION: Final[tuple[int, int]] = (3, 11)
SUPPORTED_PYTHON_VERSIONS: Final[tuple[str, ...]] = ("3.11", "3.12")

# -----------------------------------------------------------------------------
# Pinned Toolchain & Model Versions for Reproducible Evaluation
# -----------------------------------------------------------------------------
PINNED_PYTHON_VERSION: Final[str] = "3.12.10"
PINNED_PYDANTIC_VERSION: Final[str] = "2.8.2"
PINNED_RUFF_VERSION: Final[str] = "0.5.0"
PINNED_PSUTIL_VERSION: Final[str] = "6.0.0"
PINNED_OLLAMA_VERSION: Final[str] = "0.3.0"

PRIMARY_MODEL: Final[str] = "qwen2.5-coder:3b-instruct-q4_K_M"
FALLBACK_MODEL: Final[str] = "qwen2.5-coder:1.5b-instruct-q4_K_M"

# -----------------------------------------------------------------------------
# Operational Limits (Non-Sandbox Containment)
# -----------------------------------------------------------------------------
MAX_SOURCE_SIZE_BYTES: Final[int] = 256 * 1024  # 256 KB
DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0
DEFAULT_OUTPUT_BYTE_CAP: Final[int] = 512 * 1024  # 512 KB combined stdout/stderr
PROCESS_MEMORY_SAMPLE_INTERVAL_MS: Final[int] = 20

# -----------------------------------------------------------------------------
# Session and Staging Architecture
# -----------------------------------------------------------------------------
SESSION_ROOT_DIRNAME: Final[str] = "localdev"
SESSION_DIR_PREFIX: Final[str] = "session_"
SESSION_MARKER_FILENAME: Final[str] = ".localdev_session"
SESSION_TARGET_FILENAME: Final[str] = "session_target.py"
SAME_VOLUME_STAGING_DIRNAME: Final[str] = ".localdev_staging"

# -----------------------------------------------------------------------------
# Runtime Environment Policy
# -----------------------------------------------------------------------------
# Child processes launched with -E -B -P inherit only essential OS environment variables.
ENV_ALLOWLIST: Final[tuple[str, ...]] = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "COMSPEC",
    "USERNAME",
)

# -----------------------------------------------------------------------------
# Inference Token Budgets
# -----------------------------------------------------------------------------
# Hard constraint: PROMPT_BUDGET_TOKENS + OUTPUT_BUDGET_TOKENS + APPLICATION_SAFETY_MARGIN_TOKENS == CONTEXT_WINDOW_TOKENS
CONTEXT_WINDOW_TOKENS: Final[int] = 2048
PROMPT_BUDGET_TOKENS: Final[int] = 1200
OUTPUT_BUDGET_TOKENS: Final[int] = 600
APPLICATION_SAFETY_MARGIN_TOKENS: Final[int] = 248

DEFAULT_OLLAMA_URL: Final[str] = "http://127.0.0.1:11434"
CHARS_PER_TOKEN_HEURISTIC: Final[float] = 3.5
DEFAULT_KEEP_ALIVE_SECONDS: Final[int] = 0

# -----------------------------------------------------------------------------
# Patching and Guarded Mutation Limits
# -----------------------------------------------------------------------------
MAX_PATCH_EDITS: Final[int] = 8
MAX_PATCH_CHANGED_LINES: Final[int] = 80

# -----------------------------------------------------------------------------
# Complexity Vocabulary and Contract (CPython Semantics)
# -----------------------------------------------------------------------------
SUPPORTED_COMPLEXITY_CLASSES: Final[tuple[str, ...]] = (
    "O(1)",
    "O(log n)",
    "O(n)",
    "O(n log n)",
    "O(n²)",
    "O(n³)",
    "O(nm)",
    "O(2^n)",
    "UNKNOWN",
)

COMPLEXITY_CONFIDENCE_LEVELS: Final[tuple[str, ...]] = ("HIGH", "MEDIUM", "LOW")

COMPLEXITY_ABSTENTION_REASONS: Final[tuple[str, ...]] = (
    "UNKNOWN_CALL",
    "DYNAMIC_BOUNDS",
    "DYNAMIC_RECURSION",
    "EXTERNAL_DEPENDENCY",
    "UNSUPPORTED_SYNTAX",
)

# -----------------------------------------------------------------------------
# Stable CLI Exit Codes
# -----------------------------------------------------------------------------
EXIT_SUCCESS: Final[int] = 0
EXIT_TARGET_FAILURE: Final[int] = 1
EXIT_CLI_USAGE_ERROR: Final[int] = 2
EXIT_TARGET_IO_ERROR: Final[int] = 3
EXIT_TIMEOUT_RESOURCE_BREACH: Final[int] = 4
EXIT_INFERENCE_ERROR: Final[int] = 5
EXIT_ABSTENTION: Final[int] = 6

