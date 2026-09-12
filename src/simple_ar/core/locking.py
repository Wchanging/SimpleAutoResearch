"""Small cross-platform lock for one file-backed research session."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
from typing import Any


class SessionLockError(RuntimeError):
    """Base error for session lock operations."""


class SessionBusyError(SessionLockError):
    """Raised when another process already owns the session lock."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionFileLock:
    """Non-blocking OS lock with best-effort diagnostic metadata.

    The lock file is never considered active merely because it exists.  The
    operating system lock is authoritative; stale metadata is left in place so
    a caller can inspect the last known owner after a crash.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().absolute()
        self._handle: Any | None = None

    def acquire(self) -> "SessionFileLock":
        if self._handle is not None:
            raise SessionLockError("Session lock is already held by this object.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("a+b")
        except PermissionError as exc:
            # Windows may reject opening a file that another process already
            # locked before ``msvcrt.locking`` gets a chance to report it.
            raise SessionBusyError(
                f"Session lock is already held: {self.path}"
            ) from exc
        try:
            self._ensure_lock_byte(handle)
            self._lock_handle(handle)
        except Exception:
            handle.close()
            raise
        self._handle = handle
        self._write_owner()
        return self

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._unlock_handle(handle)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "SessionFileLock":
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()

    @staticmethod
    def _ensure_lock_byte(handle: Any) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)

    def _lock_handle(self, handle: Any) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            raise SessionBusyError(
                f"Session lock is already held: {self.path}"
            ) from exc

    def _unlock_handle(self, handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write_owner(self) -> None:
        handle = self._handle
        if handle is None:
            return
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": _utcnow_iso(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        handle.flush()

    @staticmethod
    def read_owner(path: str | Path) -> dict[str, Any] | None:
        """Read stale or current diagnostic metadata without acquiring a lock."""

        lock_path = Path(path).expanduser().absolute()
        if not lock_path.is_file():
            return None
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


__all__ = ["SessionBusyError", "SessionFileLock", "SessionLockError"]
