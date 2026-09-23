"""Session management and staging architecture for localdev.

Provides isolated workspace tracking in %TEMP%\\localdev\\session_<id>,
session ownership markers (.localdev_session), same-volume replacement staging,
and the strict 5-stage handle and process cleanup sequence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Self

from localdev.constants import (
    SAME_VOLUME_STAGING_DIRNAME,
    SESSION_DIR_PREFIX,
    SESSION_MARKER_FILENAME,
    SESSION_ROOT_DIRNAME,
    SESSION_TARGET_FILENAME,
)
from localdev.errors import TargetValidationError
from localdev.schemas import TargetRecord


@dataclass(frozen=True)
class SessionManifest:
    """Immutable record tracking an active localdev command session."""

    session_id: str
    target_record: TargetRecord
    session_dir: Path
    marker_file: Path
    session_target_file: Path
    manifest_file: Path
    same_volume_staging_dir: Path | None = None
    created_at_utc: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to serializable dictionary."""
        return {
            "session_id": self.session_id,
            "target_path": self.target_record.path,
            "target_absolute_path": self.target_record.absolute_path,
            "target_sha256": self.target_record.sha256,
            "session_dir": str(self.session_dir),
            "session_target_file": str(self.session_target_file),
            "manifest_file": str(self.manifest_file),
            "same_volume_staging_dir": (
                str(self.same_volume_staging_dir) if self.same_volume_staging_dir else None
            ),
            "created_at_utc": self.created_at_utc,
        }


def generate_session_id() -> str:
    """Generate a unique UUIDv4 string for a new session."""
    return str(uuid.uuid4())


def get_path_volume(path: str | Path) -> str:
    """Return the uppercase filesystem volume identifier for a path on Windows.

    Examples:
        - "C:\\Users\\Raj" -> "C:"
        - "D:\\project\\file.py" -> "D:"
        - "\\\\server\\share\\path" -> "\\\\SERVER\\SHARE"
    """
    abs_path = os.path.abspath(str(path))
    drive, _ = os.path.splitdrive(abs_path)
    if drive:
        return drive.upper()
    return os.path.split(abs_path)[0].upper()


def is_same_volume(path1: str | Path, path2: str | Path) -> bool:
    """Return True if both paths reside on the same filesystem volume."""
    return get_path_volume(path1) == get_path_volume(path2)


def resolve_same_volume_staging_dir(
    target_path: str | Path,
    session_id: str,
    base_temp_dir: Path | None = None,
) -> Path:
    """Determine the staging directory path on the exact same volume as the target.

    Win32 ReplaceFileW strictly requires the replacement candidate, the target file,
    and the backup file to reside on the same filesystem volume. Therefore, if the
    target resides on another volume (e.g. D:), candidate staging cannot reside in
    %TEMP% (typically on C:).

    Args:
        target_path: Path to the target source file.
        session_id: Unique session UUID.
        base_temp_dir: Optional override for base temporary directory.

    Returns:
        Path to dedicated same-volume staging directory.

    Raises:
        TargetValidationError: If same-volume staging violates volume invariants.
    """
    target_abs = Path(target_path).resolve()
    target_vol = get_path_volume(target_abs)
    temp_dir = base_temp_dir or Path(tempfile.gettempdir())
    temp_vol = get_path_volume(temp_dir)

    if target_vol == temp_vol:
        staging_dir = temp_dir / SESSION_ROOT_DIRNAME / f"staging_{session_id}"
    else:
        # Target resides on a different volume (e.g. D: vs C:).
        # We attempt staging at the drive root first, and fallback to the target's parent dir.
        candidate_root = Path(f"{target_vol}\\") / SAME_VOLUME_STAGING_DIRNAME / session_id
        try:
            candidate_root.mkdir(parents=True, exist_ok=True)
            staging_dir = candidate_root
        except OSError:
            # Fallback to target's parent directory on the same volume
            staging_dir = target_abs.parent / SAME_VOLUME_STAGING_DIRNAME / session_id

    # Fail-closed volume invariant assertion
    if not is_same_volume(staging_dir, target_abs):
        raise TargetValidationError(
            f"Cross-volume staging violation: candidate staging directory '{staging_dir}' "
            f"(volume {get_path_volume(staging_dir)}) does not match target volume "
            f"'{target_abs}' (volume {target_vol}).",
            details={"staging_dir": str(staging_dir), "target": str(target_abs)},
        )

    return staging_dir


def verify_session_ownership_marker(directory: Path) -> bool:
    """Verify that directory contains the .localdev_session ownership marker."""
    marker = directory / SESSION_MARKER_FILENAME
    return marker.is_file()


def safely_delete_directory(
    directory: Path,
    retries: int = 4,
    retry_delay_seconds: float = 0.05,
) -> None:
    """Safely delete a directory after verifying ownership marker and retrying on sharing violations.

    Args:
        directory: Directory to delete.
        retries: Number of retry attempts on PermissionError.
        retry_delay_seconds: Base sleep delay between retries.

    Raises:
        TargetValidationError: If ownership marker is missing.
        OSError: If directory deletion fails after all retries.
    """
    if not directory.exists():
        return

    if not verify_session_ownership_marker(directory):
        raise TargetValidationError(
            f"Cannot delete directory '{directory}': missing session ownership marker "
            f"'{SESSION_MARKER_FILENAME}'. Deletion aborted to protect non-session files.",
            details={"directory": str(directory)},
        )

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            shutil.rmtree(directory)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(retry_delay_seconds * (2**attempt))
        except OSError as exc:
            last_error = exc
            time.sleep(retry_delay_seconds * (2**attempt))

    if last_error is not None:
        raise last_error


class Session:
    """Manages the lifecycle, staging, and strict 5-stage cleanup for a single command invocation."""

    def __init__(
        self,
        target_record: TargetRecord,
        keep_session: bool = False,
        base_temp_dir: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self.session_id: Final[str] = session_id or generate_session_id()
        self.target_record: Final[TargetRecord] = target_record
        self.keep_session: bool = keep_session
        self.base_temp_dir: Final[Path] = base_temp_dir or Path(tempfile.gettempdir())

        self.session_root_dir: Final[Path] = self.base_temp_dir / SESSION_ROOT_DIRNAME
        self.session_dir: Final[Path] = self.session_root_dir / f"{SESSION_DIR_PREFIX}{self.session_id}"
        self.marker_file: Final[Path] = self.session_dir / SESSION_MARKER_FILENAME
        self.session_target_file: Final[Path] = self.session_dir / SESSION_TARGET_FILENAME
        self.manifest_file: Final[Path] = self.session_dir / "manifest.json"

        self.same_volume_staging_dir: Path | None = None
        self._staging_dirs: list[Path] = []
        self._processes: list[Any] = []
        self._handles: list[Any] = []
        self._pipes: list[Any] = []
        self._cleaned_up: bool = False
        self._manifest: SessionManifest | None = None

    def initialize(self) -> SessionManifest:
        """Create session directory, write ownership marker, copy target, and save manifest."""
        self.session_dir.mkdir(parents=True, exist_ok=True)

        created_at = datetime.now(timezone.utc).isoformat()

        # Write ownership marker
        marker_content = {
            "session_id": self.session_id,
            "created_at_utc": created_at,
            "target": self.target_record.absolute_path,
        }
        self.marker_file.write_text(json.dumps(marker_content, indent=2), encoding="utf-8")

        # Copy target file to session_target.py
        shutil.copy2(self.target_record.absolute_path, self.session_target_file)

        manifest = SessionManifest(
            session_id=self.session_id,
            target_record=self.target_record,
            session_dir=self.session_dir,
            marker_file=self.marker_file,
            session_target_file=self.session_target_file,
            manifest_file=self.manifest_file,
            same_volume_staging_dir=self.same_volume_staging_dir,
            created_at_utc=created_at,
        )

        self.manifest_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        self._manifest = manifest
        return manifest

    def create_same_volume_staging_dir(self) -> Path:
        """Create and register a dedicated candidate staging directory on the target volume."""
        staging_dir = resolve_same_volume_staging_dir(
            target_path=self.target_record.absolute_path,
            session_id=self.session_id,
            base_temp_dir=self.base_temp_dir,
        )
        staging_dir.mkdir(parents=True, exist_ok=True)

        # Place ownership marker in staging dir
        staging_marker = staging_dir / SESSION_MARKER_FILENAME
        staging_marker.write_text(
            json.dumps({"session_id": self.session_id, "type": "staging"}, indent=2),
            encoding="utf-8",
        )

        self.same_volume_staging_dir = staging_dir
        if staging_dir not in self._staging_dirs:
            self._staging_dirs.append(staging_dir)
        return staging_dir

    def register_process(self, proc: Any) -> None:
        """Register a subprocess or worker to be stopped during cleanup."""
        self._processes.append(proc)

    def register_pipe(self, pipe: Any) -> None:
        """Register a pipe or I/O handle to be closed during cleanup."""
        self._pipes.append(pipe)

    def register_handle(self, handle: Any) -> None:
        """Register an OS handle or cleanup callback to be executed during cleanup."""
        self._handles.append(handle)

    def cleanup(self) -> None:
        """Execute strict 5-stage handle, process, and workspace cleanup sequence.

        Stages:
            1. Stop/terminate target process tree.
            2. Close pipes and open file handles (stdin, stdout, stderr).
            3. Wait for process exit.
            4. Close Job Object and process handles.
            5. Delete session directory and same-volume staging (guarded by marker).
        """
        if self._cleaned_up:
            return

        # Stage 1: Stop/terminate processes
        for proc in self._processes:
            try:
                if hasattr(proc, "poll") and proc.poll() is None:
                    proc.terminate()
            except OSError:
                pass

        # Stage 2: Close pipes and I/O handles
        for pipe in self._pipes:
            try:
                if hasattr(pipe, "close"):
                    pipe.close()
            except OSError:
                pass

        for proc in self._processes:
            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(proc, stream_name, None)
                if stream is not None and hasattr(stream, "close"):
                    try:
                        stream.close()
                    except OSError:
                        pass

        # Stage 3: Wait for process termination
        for proc in self._processes:
            try:
                if hasattr(proc, "wait"):
                    try:
                        proc.wait(timeout=3.0)
                    except (subprocess.TimeoutExpired, OSError):
                        if hasattr(proc, "kill"):
                            proc.kill()
                            proc.wait(timeout=2.0)
            except OSError:
                pass

        # Stage 4: Close Job Object / OS handles
        for handle in self._handles:
            try:
                if callable(handle):
                    handle()
                elif hasattr(handle, "close"):
                    handle.close()
                elif hasattr(handle, "Close"):
                    handle.Close()
            except OSError:
                pass

        # Stage 5: Delete session and staging directories
        if self.keep_session:
            self._cleaned_up = True
            return

        for staging_dir in self._staging_dirs:
            safely_delete_directory(staging_dir)

        if self.session_dir.exists():
            safely_delete_directory(self.session_dir)

        self._cleaned_up = True

    def __enter__(self) -> Self:
        self.initialize()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.cleanup()
