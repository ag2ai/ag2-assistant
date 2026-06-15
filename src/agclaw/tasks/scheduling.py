"""Task scheduling — one-shot and recurring.

A scheduled task sits in status SCHEDULED with `scheduled_for` (ISO datetime) and,
for recurring jobs, a `recurrence` interval spec. A deterministic poll loop
(`Scheduler`, no LLM) checks for due tasks and fires a callback:

- one-shot  → the task itself runs, then follows the normal lifecycle.
- recurring → a fresh run is spawned for this occurrence and the template is
  re-armed for the next one (so each run is its own task in the history).

Recurrence is interval-based and anchored to `scheduled_for`, so "daily" from a
9am anchor keeps firing at 9am. Supported specs: "hourly" / "daily" / "weekly",
or "every N minute(s)/hour(s)/day(s)/week(s)".
"""

import asyncio
import re
from datetime import datetime, timedelta

from agclaw.tasks.model import TaskStatus

_NAMED = {
    "minutely": timedelta(minutes=1),
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}
_UNITS = {
    "minute": timedelta(minutes=1), "minutes": timedelta(minutes=1), "min": timedelta(minutes=1),
    "hour": timedelta(hours=1), "hours": timedelta(hours=1), "hr": timedelta(hours=1),
    "day": timedelta(days=1), "days": timedelta(days=1),
    "week": timedelta(weeks=1), "weeks": timedelta(weeks=1),
}


def parse_recurrence(spec: str | None) -> timedelta | None:
    """Turn a recurrence spec into an interval, or None if it isn't recurring/valid."""
    if not spec:
        return None
    s = spec.strip().lower()
    if s in _NAMED:
        return _NAMED[s]
    m = re.fullmatch(r"every\s+(\d+)?\s*([a-z]+)", s)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        unit = _UNITS.get(m.group(2))
        if unit and n > 0:
            return unit * n
    return None


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    return dt.astimezone() if dt.tzinfo else dt.astimezone()


def is_due(scheduled_for: str | None, now: datetime) -> bool:
    """True if a scheduled time has arrived."""
    dt = _parse_dt(scheduled_for)
    return dt is not None and dt <= now


def next_occurrence(recurrence: str | None, anchor: str | None, now: datetime) -> datetime | None:
    """The next future occurrence at/after `now`, stepping from `anchor` by the
    interval (skips missed slots if the scheduler was down). None if not recurring."""
    delta = parse_recurrence(recurrence)
    start = _parse_dt(anchor)
    if delta is None or start is None:
        return None
    nxt = start + delta
    while nxt <= now:
        nxt += delta
    return nxt


class Scheduler:
    """Polls the store for due SCHEDULED tasks and fires them (deterministic)."""

    def __init__(self, store, fire, interval: float = 30.0) -> None:
        self._store = store
        self._fire = fire            # async (task_id) -> None
        self._interval = interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        # Wait one interval before the first scan (so startup never races other
        # store users), then poll until stopped.
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            if self._stop.is_set():
                break
            try:
                await self.tick()
            except Exception:
                pass  # a bad record must never kill the loop

    async def tick(self, now: datetime | None = None) -> list[str]:
        """One scan: fire every due scheduled task. Returns the ids fired."""
        now = now or datetime.now().astimezone()
        fired = []
        for t in await self._store.list_all():
            if t.status == TaskStatus.SCHEDULED and is_due(t.scheduled_for, now):
                await self._fire(t.id)
                fired.append(t.id)
        return fired

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
