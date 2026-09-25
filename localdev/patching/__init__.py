"""Structured editing and safe patch application module.

Provides:
- edit_schema: EditOperationType, EditOperation, and EditProposalRecord.
- edit_validator: EditValidator, EditValidationResult.
- applier: PatchApplier, PatchCandidate.
- diff_renderer: render_unified_diff, colorize_unified_diff.
- backup: get_backup_path, verify_backup, restore_backup, remove_backup.
- atomic_write: atomic_replace_file, win32_replace_file, assert_same_volume,
  get_volume_for_path, get_same_volume_staging_dir, AtomicReplacementResult.
"""

from localdev.patching.applier import (
    PatchApplier,
    PatchCandidate,
)
from localdev.patching.atomic_write import (
    AtomicReplacementResult,
    assert_same_volume,
    atomic_replace_file,
    get_same_volume_staging_dir,
    get_volume_for_path,
    win32_replace_file,
)
from localdev.patching.backup import (
    get_backup_path,
    remove_backup,
    restore_backup,
    verify_backup,
)
from localdev.patching.diff_renderer import (
    colorize_unified_diff,
    render_unified_diff,
)
from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.patching.edit_validator import (
    EditValidationResult,
    EditValidator,
)

__all__ = [
    "AtomicReplacementResult",
    "EditOperation",
    "EditOperationType",
    "EditProposalRecord",
    "EditValidationResult",
    "EditValidator",
    "PatchApplier",
    "PatchCandidate",
    "assert_same_volume",
    "atomic_replace_file",
    "colorize_unified_diff",
    "get_backup_path",
    "get_same_volume_staging_dir",
    "get_volume_for_path",
    "remove_backup",
    "render_unified_diff",
    "restore_backup",
    "verify_backup",
    "win32_replace_file",
]
