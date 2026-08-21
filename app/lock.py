"""Single-run file lock.

Two scheduled runs must never mutate state files concurrently. The lock is an
exclusively-created file containing the owner's PID and start time. A lock
whose owner is dead, or which is older than STALE_AFTER_SECONDS, is broken —
laptops sleep and processes get killed, so abandoned locks are normal.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STALE_AFTER_SECONDS = 30 * 60


class AlreadyRunning(Exception):
    """Another live run owns the lock; the caller should exit safely."""


class RunLock:
    def __init__(self, lock_file: Path, stale_after: float = STALE_AFTER_SECONDS):
        self.lock_file = lock_file
        self.stale_after = stale_after

    def __enter__(self) -> "RunLock":
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._try_acquire():
            self._break_if_stale()
            if not self._try_acquire():
                raise AlreadyRunning(f"lock held: {self.lock_file}")
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            self.lock_file.unlink()
        except FileNotFoundError:
            pass

    def _try_acquire(self) -> bool:
        try:
            fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": time.time()}, f)
        return True

    def _break_if_stale(self) -> None:
        try:
            info = json.loads(self.lock_file.read_text(encoding="utf-8"))
            pid = int(info["pid"])
            started_at = float(info["started_at"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            # Unreadable lock: only age can tell us anything.
            try:
                if time.time() - self.lock_file.stat().st_mtime > self.stale_after:
                    self.lock_file.unlink(missing_ok=True)
            except OSError:
                pass
            return

        if time.time() - started_at > self.stale_after or not _pid_alive(pid):
            self.lock_file.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
