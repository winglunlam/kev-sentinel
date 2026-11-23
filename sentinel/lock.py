"""A tiny PID lock so overlapping cron runs cannot stack up.

Only one check should run per config at a time: a slow endpoint plus a frequent
cron can otherwise pile up processes and duplicate alerts.
"""

from __future__ import annotations

import os
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class FileLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(
                    str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError:
                try:
                    pid = int(self.path.read_text(encoding="utf-8").strip() or "0")
                except (OSError, ValueError):
                    pid = 0
                if _pid_alive(pid):
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self.acquired = False

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise RuntimeError(f"lock is held: {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
