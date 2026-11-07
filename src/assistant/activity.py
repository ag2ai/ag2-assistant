"""What the process is doing right now, for a host that decides whether it may sleep.

``Activity`` tallies open holds: ``work`` (a chat turn, a task run, post-turn
background work, an ACP turn), ``scheduled`` (tasks with a future ``next_run_at``)
and ``human`` (a turn parked on a question). ``snapshot()`` is the wire shape of
``GET /api/activity``; with a file path it is also written atomically on every change
of the holds, after a short grace once the last work hold closes; a new process writes
its empty state at once, so a crashed predecessor's file never outlives it.

The registry describes one process: the gateway, which owns every profile's turns,
runs and ACP listeners. ``ag2-assistant acp-serve`` is a separate per-client stdio
process an editor launches on the same machine and is not part of this signal.
"""

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Callable, Generator
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ag2.middleware.base import BaseMiddleware

from assistant.observability import log_suppressed

if TYPE_CHECKING:
    from ag2.annotations import Context
    from ag2.events import BaseEvent

WORK = "work"
SCHEDULED = "scheduled"
HUMAN = "human"
IDLE_GRACE_S = 2.5
RETRY_S = 1.0  # first retry after a failed file write; doubles up to RETRY_MAX_S
RETRY_MAX_S = 30.0


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _HumanWatch:
    """Wraps an asker's ``has_pending`` so polling it opens and closes a human hold."""

    def __init__(self, activity: "Activity", pending, deadline, fallback_s: float) -> None:
        self._activity = activity
        self._pending = pending
        self._deadline = deadline
        self._fallback_s = fallback_s
        self._token: int | None = None

    def __call__(self) -> bool:
        pending = bool(self._pending())
        if pending and self._token is None:
            until = self._deadline()
            if until is None:
                until = time.time() + self._fallback_s
            self._token = self._activity.open_human(until)
        elif not pending and self._token is not None:
            self.close()
        return pending

    def close(self) -> None:
        if self._token is not None:
            self._activity.close_human(self._token)
            self._token = None


class Activity:
    """Open holds on the process, their snapshot, and the optional state file."""

    def __init__(
        self,
        file: str | os.PathLike[str] | None = None,
        *,
        idle_grace_s: float = IDLE_GRACE_S,
        retry_s: float = RETRY_S,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._file = Path(file) if file else None
        self._grace = idle_grace_s
        self._retry_s = retry_s
        self._retry_delay = retry_s
        self._retry_timer: asyncio.TimerHandle | None = None
        self._dirty = False  # the file does not hold the latest state (a write failed)
        self._clock = clock
        self._work: dict[str, int] = {}
        self._human: dict[int, float] = {}
        self._next_token = 0
        # key -> (count, earliest next) when known; None while a refresh has failed
        self._scheduled: dict[str, tuple[int, str | None] | None] = {}
        self._idle_since: datetime | None = clock()
        self._was_busy = False
        self._last_written: list[dict] | None = None
        self._idle_timer: asyncio.TimerHandle | None = None
        self._pending: list[dict] | None = None  # the holds the timed idle write carries
        if self._file is not None:
            self._write()  # a fresh process replaces whatever its predecessor left

    # ---- holds ----

    def acquire(self, detail: str) -> Callable[[], None]:
        """Open a ``work`` hold with this ``detail`` now; call the result once to close it."""
        self._work[detail] = self._work.get(detail, 0) + 1
        self._changed()
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._work[detail] -= 1
            if self._work[detail] <= 0:
                del self._work[detail]
            self._changed()

        return release

    @contextlib.contextmanager
    def track(self, detail: str) -> Generator[None]:
        """Hold ``work`` with this ``detail`` (turn, run, background, acp_turn) for the block."""
        release = self.acquire(detail)
        try:
            yield
        finally:
            release()

    def track_async(self, detail: str) -> AbstractAsyncContextManager[None]:
        """``track`` as an async context manager, for AG2's middleware scope."""

        @contextlib.asynccontextmanager
        async def scope():
            with self.track(detail):
                yield

        return scope()

    def open_human(self, until: float) -> int:
        """Open a human hold that lapses at ``until`` (epoch seconds); returns its token."""
        self._next_token += 1
        self._human[self._next_token] = until
        self._changed()
        return self._next_token

    def close_human(self, token: int) -> None:
        if self._human.pop(token, None) is not None:
            self._changed()

    def human_watch(self, pending, deadline=None, *, fallback_s: float) -> _HumanWatch:
        """A ``has_pending`` stand-in that opens a human hold while the asker waits.
        ``deadline`` returns the asker's epoch deadline, or None for ``now + fallback_s``."""
        return _HumanWatch(self, pending, deadline or (lambda: None), fallback_s)

    def set_scheduled(self, key: str, count: int, next_at: str | None) -> None:
        """Record one profile's scheduled tasks: how many, and the earliest ``next_run_at``."""
        value = (count, next_at if count else None)
        if self._scheduled.get(key, (0, None)) == value:
            return
        if count:
            self._scheduled[key] = value
        else:
            self._scheduled.pop(key, None)
        self._changed()

    def set_scheduled_unknown(self, key: str) -> None:
        """A profile's schedule could not be read: a scheduled hold with no count stands
        in until a refresh succeeds, so a host never sleeps on a guess."""
        if self._scheduled.get(key, (0, None)) is None:
            return
        self._scheduled[key] = None
        self._changed()

    # ---- snapshot ----

    @property
    def busy(self) -> bool:
        return bool(self._work)

    def holds(self) -> list[dict]:
        out: list[dict] = [
            {"kind": WORK, "count": count, "detail": detail}
            for detail, count in sorted(self._work.items())
        ]
        known = [value for value in self._scheduled.values() if value is not None]
        total = sum(count for count, _ in known)
        if total or len(known) < len(self._scheduled):
            hold: dict = {"kind": SCHEDULED}
            if total:
                hold["count"] = total  # omitted while a profile's schedule is unknown
            nexts = [nxt for _, nxt in known if nxt]
            if nexts:
                hold["next"] = min(nexts)
            out.append(hold)
        out.extend(
            {"kind": HUMAN, "until": iso_utc(datetime.fromtimestamp(until, timezone.utc))}
            for until in self._human.values()
        )
        return out

    def flush(self) -> None:
        """Rewrite the file now if a write failed earlier (a poll is a chance to recover)."""
        if self._dirty and self._idle_timer is None:
            self._write()

    def snapshot(self) -> dict:
        return {
            "ts": iso_utc(self._clock()),
            "idle_since": None if self._idle_since is None else iso_utc(self._idle_since),
            "holds": self.holds(),
        }

    # ---- change tracking + file ----

    def _changed(self) -> None:
        if self._work and self._idle_since is not None:
            self._idle_since = None
        elif not self._work and self._idle_since is None:
            self._idle_since = self._clock()
        busy = bool(self._work)
        released = self._was_busy and not busy  # the last work hold just closed
        self._was_busy = busy
        if self._file is None:
            return
        holds = self.holds()
        if self._idle_timer is not None:
            if holds == self._pending:
                return  # the timed write already carries this state; let it land
            self._idle_timer.cancel()
            self._idle_timer = None
            self._pending = None
        if holds == self._last_written:
            return
        if not released or self._grace <= 0:
            self._write()  # only the end of work waits; everything else lands at once
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._write()
            return
        self._pending = holds
        self._idle_timer = loop.call_later(self._grace, self._write)

    def _write(self) -> None:
        self._idle_timer = None
        self._pending = None
        if self._file is None:
            return
        snapshot = self.snapshot()
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_name(f".{self._file.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(snapshot))
            os.replace(tmp, self._file)
        except Exception as exc:
            log_suppressed("activity file write", exc, path=str(self._file))
            self._dirty = True
            self._schedule_retry()
            return
        self._last_written = snapshot["holds"]
        self._dirty = False
        self._retry_delay = self._retry_s
        if self._retry_timer is not None:
            self._retry_timer.cancel()
            self._retry_timer = None

    def _schedule_retry(self) -> None:
        if self._retry_timer is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop to wait on; the next change or flush retries
        delay = self._retry_delay
        self._retry_delay = min(delay * 2, RETRY_MAX_S)
        self._retry_timer = loop.call_later(delay, self._retry_write)

    def _retry_write(self) -> None:
        self._retry_timer = None
        self.flush()


class ActivityMiddleware:
    """AG2 ``ScopedMiddlewareFactory`` whose scope holds ``work`` for the whole agent
    turn, so turns the gateway does not drive itself (ACP) still count."""

    def __init__(self, activity: Activity, detail: str = "acp_turn") -> None:
        self._activity = activity
        self._detail = detail

    def __call__(self, event: "BaseEvent", context: "Context") -> BaseMiddleware:
        return BaseMiddleware(event, context)

    def scope(self, event: Any, context: Any) -> AbstractAsyncContextManager[None]:
        return self._activity.track_async(self._detail)
