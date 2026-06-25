"""Cross-process leader lock so exactly one task scheduler runs per data dir.

POSIX ``flock`` is released by the OS when the holder exits, so a dead leader
never blocks the next. Without ``fcntl`` (e.g. Windows) it's a no-op that always
acquires — the opt-in ``scheduler`` flag on ``TaskService.start`` stays the guard.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("ag2assistant.tasks")

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]


class SchedulerLock:
    """An advisory, process-lifetime lock over a lock file in the data dir."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._fh = None

    def acquire(self) -> bool:
        """True if we now hold the lock (or none is available); False if taken."""
        if fcntl is None:  # pragma: no cover - non-POSIX
            return True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self._path, "w")
        except OSError as exc:  # pragma: no cover
            logger.warning("scheduler lock unavailable (%s); running without it", exc)
            return True
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        """Idempotent; the OS would release on exit anyway."""
        if self._fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:  # pragma: no cover
            pass
        finally:
            self._fh.close()
            self._fh = None
