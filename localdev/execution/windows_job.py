"""Windows Job Object lifecycle wrapper and process containment.

Enforces robust process-tree containment for controlled target execution using
Win32 Job Objects configured with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

In nested environments (such as VS Code integrated terminal, Windows Terminal,
or CI runners) where outer jobs lack breakaway permissions
(JOB_OBJECT_LIMIT_BREAKAWAY_OK), Job Object assignment fails by design
(ERROR_ACCESS_DENIED, code 5). In accordance with the Runtime and Isolation
Contract, the psutil fallback is the expected default containment mechanism
(not a rare edge case).
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from types import TracebackType
from typing import Any, Final, Self

from localdev.errors import (
    JobObjectAssignmentError,
    JobObjectConfigurationError,
    JobObjectCreationError,
    JobObjectError,
)

# Win32 Limit Flags
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final[int] = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK: Final[int] = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK: Final[int] = 0x00001000

# Win32 Info Classes
JobObjectExtendedLimitInformation: Final[int] = 9

# Process Access Rights
PROCESS_TERMINATE: Final[int] = 0x0001
PROCESS_SET_QUOTA: Final[int] = 0x0100
PROCESS_QUERY_INFORMATION: Final[int] = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000

# Win32 Error Codes
ERROR_SUCCESS: Final[int] = 0
ERROR_ACCESS_DENIED: Final[int] = 5
ERROR_INVALID_HANDLE: Final[int] = 6
ERROR_INVALID_PARAMETER: Final[int] = 87
ERROR_ALREADY_EXISTS: Final[int] = 183


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryLimit", ctypes.c_size_t),
        ("PeakJobMemoryLimit", ctypes.c_size_t),
    ]


_KERNEL32: ctypes.WinDLL | None = None


def get_kernel32() -> ctypes.WinDLL:
    """Return kernel32 WinDLL instance configured with strict argtypes and restypes."""
    global _KERNEL32
    if _KERNEL32 is not None:
        return _KERNEL32

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]

    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]

    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]

    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]

    _KERNEL32 = kernel32
    return _KERNEL32


class WindowsJobObject:
    """Encapsulates a native Win32 Job Object for process-tree lifecycle management.

    Configures JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so that closing the job handle
    (or abnormal termination of localdev) causes the Windows kernel to terminate
    all processes assigned to the job and all their descendants immediately.
    """

    def __init__(
        self,
        name: str | None = None,
        kill_on_close: bool = True,
        breakaway_ok: bool = False,
        silent_breakaway_ok: bool = False,
    ) -> None:
        if sys.platform != "win32":
            raise JobObjectError("Windows Job Objects are only supported on Windows.")

        self._kernel32 = get_kernel32()
        self._name = name
        self._handle: int | None = None

        raw_handle = self._kernel32.CreateJobObjectW(None, name)
        if not raw_handle:
            err = ctypes.get_last_error()
            msg = ctypes.FormatError(err).strip()
            raise JobObjectCreationError(
                f"Failed to create Windows Job Object '{name or '<anonymous>'}': {msg} (code {err})",
                win_error_code=err,
            )

        self._handle = int(raw_handle)

        # Configure extended limit information
        try:
            limit_flags = 0
            if kill_on_close:
                limit_flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if breakaway_ok:
                limit_flags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK
            if silent_breakaway_ok:
                limit_flags |= JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = limit_flags

            success = self._kernel32.SetInformationJobObject(
                self._handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not success:
                err = ctypes.get_last_error()
                msg = ctypes.FormatError(err).strip()
                raise JobObjectConfigurationError(
                    f"Failed to configure Job Object limits: {msg} (code {err})",
                    win_error_code=err,
                )
        except Exception:
            self.close()
            raise

    @property
    def handle(self) -> int | None:
        """Raw Win32 handle value, or None if closed."""
        return self._handle

    @property
    def is_closed(self) -> bool:
        """True if the Job Object handle has been closed."""
        return self._handle is None

    @property
    def name(self) -> str | None:
        """Name of the Job Object, if named."""
        return self._name

    def assign_process(self, process: int | subprocess.Popen[Any]) -> None:
        """Assign a process (and all its future descendants) to this Job Object.

        Args:
            process: Target subprocess PID or Popen object.

        Raises:
            JobObjectError: If the job object is closed.
            JobObjectAssignmentError: If process assignment fails. If the failure
                is due to nested job constraints (ERROR_ACCESS_DENIED, code 5),
                is_nested_restriction is set to True.
        """
        if self._handle is None:
            raise JobObjectError("Cannot assign process: Job Object handle is closed.")

        pid = process.pid if isinstance(process, subprocess.Popen) else int(process)

        desired_access = PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
        h_proc = self._kernel32.OpenProcess(desired_access, False, pid)
        if not h_proc:
            err = ctypes.get_last_error()
            msg = ctypes.FormatError(err).strip()
            raise JobObjectAssignmentError(
                f"Failed to open process {pid} for Job Object assignment: {msg} (code {err})",
                win_error_code=err,
            )

        try:
            success = self._kernel32.AssignProcessToJobObject(self._handle, h_proc)
            if not success:
                err = ctypes.get_last_error()
                msg = ctypes.FormatError(err).strip()
                is_nested = (err == ERROR_ACCESS_DENIED)
                nested_hint = (
                    " (Process may be trapped in a nested Job Object hierarchy without breakaway permissions, "
                    "such as in VS Code terminal or CI environments. Fall back to psutil.)"
                    if is_nested
                    else ""
                )
                raise JobObjectAssignmentError(
                    f"Failed to assign process {pid} to Job Object: {msg} (code {err}){nested_hint}",
                    win_error_code=err,
                    is_nested_restriction=is_nested,
                )
        finally:
            self._kernel32.CloseHandle(h_proc)

    def is_process_in_job(self, process: int | subprocess.Popen[Any]) -> bool:
        """Check if a process is currently assigned to this Job Object.

        Args:
            process: Target subprocess PID or Popen object.

        Returns:
            True if process is confirmed in this job object, False otherwise.
        """
        if self._handle is None:
            return False

        pid = process.pid if isinstance(process, subprocess.Popen) else int(process)
        h_proc = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_proc:
            return False

        try:
            result = wintypes.BOOL()
            success = self._kernel32.IsProcessInJob(h_proc, self._handle, ctypes.byref(result))
            if not success:
                return False
            return bool(result.value)
        finally:
            self._kernel32.CloseHandle(h_proc)

    def terminate(self, exit_code: int = 1) -> None:
        """Immediately terminate all processes currently in this Job Object.

        Args:
            exit_code: Exit code to assign to terminated processes.
        """
        if self._handle is None:
            return

        success = self._kernel32.TerminateJobObject(self._handle, exit_code)
        if not success:
            err = ctypes.get_last_error()
            # If all processes already exited or handle is invalid, ignore gracefully
            if err not in (ERROR_ACCESS_DENIED, ERROR_INVALID_HANDLE):
                msg = ctypes.FormatError(err).strip()
                raise JobObjectError(
                    f"Failed to terminate Job Object: {msg} (code {err})",
                    win_error_code=err,
                )

    def close(self) -> None:
        """Deterministically close the Job Object handle.

        If JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE was configured, closing the handle
        causes Windows to immediately terminate all assigned processes.
        Idempotent: safe to call multiple times.
        """
        if self._handle is not None:
            handle = self._handle
            self._handle = None
            self._kernel32.CloseHandle(handle)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except (OSError, AttributeError):
            pass


def can_create_job_object() -> bool:
    """Probe whether the current execution environment can create Win32 Job Objects."""
    if sys.platform != "win32":
        return False
    try:
        with WindowsJobObject():
            return True
    except JobObjectError:
        return False
