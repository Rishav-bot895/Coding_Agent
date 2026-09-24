"""Asynchronous pipe draining and output byte capping for localdev subprocesses.

Enforces:
- Combined output byte limit across stdout and stderr (default: 512 KB).
- Asynchronous pipe draining to prevent pipe buffer deadlocks.
- Bounded parent memory consumption during runaway stdout/stderr floods.
- Partial output preservation on limit breaches.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import IO


class OutputCollector:
    """Thread-safe collector for stdout and stderr enforcing a combined byte cap."""

    def __init__(self, byte_cap: int) -> None:
        """Initialize collector with a maximum combined byte capacity.

        Args:
            byte_cap: Maximum combined bytes allowed across stdout and stderr.

        Raises:
            ValueError: If byte_cap is not positive.
        """
        if byte_cap <= 0:
            raise ValueError(f"Output byte cap must be positive, got {byte_cap}")
        self.byte_cap = byte_cap
        self._lock = threading.Lock()
        self._stdout_chunks: list[bytes] = []
        self._stderr_chunks: list[bytes] = []
        self._total_bytes: int = 0
        self._truncated: bool = False
        self.cap_reached_event = threading.Event()

    def append_stdout(self, chunk: bytes) -> None:
        """Append a chunk of stdout bytes under the combined byte cap."""
        with self._lock:
            if self._truncated:
                return
            allowed = self.byte_cap - self._total_bytes
            if len(chunk) > allowed:
                self._stdout_chunks.append(chunk[:allowed])
                self._total_bytes += allowed
                self._truncated = True
                self.cap_reached_event.set()
            else:
                self._stdout_chunks.append(chunk)
                self._total_bytes += len(chunk)
                if self._total_bytes >= self.byte_cap:
                    self._truncated = True
                    self.cap_reached_event.set()

    def append_stderr(self, chunk: bytes) -> None:
        """Append a chunk of stderr bytes under the combined byte cap."""
        with self._lock:
            if self._truncated:
                return
            allowed = self.byte_cap - self._total_bytes
            if len(chunk) > allowed:
                self._stderr_chunks.append(chunk[:allowed])
                self._total_bytes += allowed
                self._truncated = True
                self.cap_reached_event.set()
            else:
                self._stderr_chunks.append(chunk)
                self._total_bytes += len(chunk)
                if self._total_bytes >= self.byte_cap:
                    self._truncated = True
                    self.cap_reached_event.set()

    @property
    def is_truncated(self) -> bool:
        """Return True if combined output breached the byte cap."""
        with self._lock:
            return self._truncated

    @property
    def total_bytes(self) -> int:
        """Return total combined bytes captured across stdout and stderr."""
        with self._lock:
            return self._total_bytes

    def get_stdout_bytes(self) -> bytes:
        """Return accumulated stdout bytes up to the cap."""
        with self._lock:
            return b"".join(self._stdout_chunks)

    def get_stderr_bytes(self) -> bytes:
        """Return accumulated stderr bytes up to the cap."""
        with self._lock:
            return b"".join(self._stderr_chunks)

    def get_stdout_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        """Decode accumulated stdout bytes using replacing error handler."""
        return self.get_stdout_bytes().decode(encoding, errors=errors)

    def get_stderr_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        """Decode accumulated stderr bytes using replacing error handler."""
        return self.get_stderr_bytes().decode(encoding, errors=errors)


class PipeDrainer:
    """Asynchronously drains subprocess stdout, stderr, and feeds stdin without deadlocks."""

    def __init__(
        self,
        stdout_stream: IO[bytes] | None,
        stderr_stream: IO[bytes] | None,
        collector: OutputCollector,
        stdin_stream: IO[bytes] | None = None,
        stdin_data: bytes | None = None,
    ) -> None:
        self._stdout_stream = stdout_stream
        self._stderr_stream = stderr_stream
        self._collector = collector
        self._stdin_stream = stdin_stream
        self._stdin_data = stdin_data

        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stdin_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start asynchronous reader and writer threads."""
        if self._stdout_stream is not None:
            self._stdout_thread = threading.Thread(
                target=self._drain_stream,
                args=(self._stdout_stream, self._collector.append_stdout),
                daemon=True,
                name="localdev-stdout-drainer",
            )
            self._stdout_thread.start()

        if self._stderr_stream is not None:
            self._stderr_thread = threading.Thread(
                target=self._drain_stream,
                args=(self._stderr_stream, self._collector.append_stderr),
                daemon=True,
                name="localdev-stderr-drainer",
            )
            self._stderr_thread.start()

        if self._stdin_stream is not None and self._stdin_data is not None:
            self._stdin_thread = threading.Thread(
                target=self._feed_stdin,
                args=(self._stdin_stream, self._stdin_data),
                daemon=True,
                name="localdev-stdin-feeder",
            )
            self._stdin_thread.start()

    def _drain_stream(
        self,
        stream: IO[bytes],
        append_fn: Callable[[bytes], None],
    ) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                append_fn(chunk)
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except (ValueError, OSError):
                pass

    def _feed_stdin(self, stream: IO[bytes], data: bytes) -> None:
        try:
            stream.write(data)
            stream.flush()
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except (ValueError, OSError):
                pass

    def wait(self, timeout: float = 1.0) -> None:
        """Wait for reader and writer threads to complete up to timeout."""
        if self._stdout_thread is not None and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=timeout)
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=timeout)
        if self._stdin_thread is not None and self._stdin_thread.is_alive():
            self._stdin_thread.join(timeout=timeout)
