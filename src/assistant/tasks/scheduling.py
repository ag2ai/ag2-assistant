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

from assistant.tasks.model import TaskStatus

_NAMED = {
    "minutely": timedelta(minutes=1),
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}
_UNITS = {
    "minute": timedelta(minutes=1),
    "minutes": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "hours": timedelta(hours=1),
    "hr": timedelta(hours=1),
    "day": timedelta(days=1),
    "days": timedelta(days=1),
    "week": timedelta(weeks=1),
    "weeks": timedelta(weeks=1),
}
_WEEKDAY = {  # Mon=0 … Sun=6
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


def parse_recurrence(spec: str | None) -> dict | None:
    """Normalise a recurrence spec, or None if it isn't recurring/valid.

    Returns one of:
      {"kind": "interval", "delta": timedelta}        — daily/hourly/weekly/every N units
      {"kind": "days", "days": frozenset[int]}        — weekdays/weekends/'mon,wed,fri'
    Day-of-week recurrences fire at the anchor's time-of-day on each matching day.
    """
    if not spec:
        return None
    s = spec.strip().lower()
    if s in ("weekday", "weekdays", "every weekday", "every weekdays", "weekdays only"):
        return {"kind": "days", "days": frozenset({0, 1, 2, 3, 4})}
    if s in ("weekend", "weekends", "every weekend"):
        return {"kind": "days", "days": frozenset({5, 6})}
    # explicit day list: "mon,wed,fri", "every monday and friday", "tuesdays"
    body = s
    for p in ("every ", "on ", "each "):
        if body.startswith(p):
            body = body[len(p) :]
    days, non_day = set(), False
    for tok in re.split(r"[,/&]|\band\b|\s+", body):
        tok = tok.strip().rstrip("s")
        if not tok:
            continue
        if tok in _WEEKDAY:
            days.add(_WEEKDAY[tok])
        else:
            non_day = True
    if days and not non_day:
        return {"kind": "days", "days": frozenset(days)}
    if s in _NAMED:
        return {"kind": "interval", "delta": _NAMED[s]}
    m = re.fullmatch(r"every\s+(\d+)?\s*([a-z]+)", s)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        unit = _UNITS.get(m.group(2))
        if unit and n > 0:
            return {"kind": "interval", "delta": unit * n}
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


def validate_schedule(
    when: str | None, recurrence: str | None, *, require_when: bool = False
) -> str | None:
    """Validate agent-supplied schedule args; return a correctable error message or
    None if they're fine. Catches a malformed `when`/`recurrence` at the tool
    boundary so it can't silently store a schedule that never fires.

    `require_when=True` for first scheduling (a run time is mandatory); reschedule
    allows an empty `when` (keep) and `recurrence` "off" (stop) / empty (keep).
    """
    w = (when or "").strip()
    if w and _parse_dt(w) is None:
        return (
            f"Couldn't parse '{when}' as a date/time. Use an ISO 8601 datetime from "
            "your environment clock, e.g. 2026-06-20T17:00:00+10:00."
        )
    if require_when and not w:
        return "A first run time is required — give an ISO 8601 datetime."
    r = (recurrence or "").strip().lower()
    if r and r != "off" and parse_recurrence(r) is None:
        return (
            f"Couldn't parse '{recurrence}' as a recurrence. Use daily / hourly / "
            "weekly, 'every N units', 'weekdays', 'weekends', or 'mon,wed,fri'."
        )
    return None


def _at_time_of(day: datetime, t: datetime) -> datetime:
    return day.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)


def next_occurrence(recurrence: str | None, anchor: str | None, now: datetime) -> datetime | None:
    """The next future occurrence after `now`. For intervals, step from `anchor`
    (skips missed slots). For day-of-week recurrences, the next matching weekday
    at the anchor's time-of-day. None if not recurring/valid."""
    spec = parse_recurrence(recurrence)
    start = _parse_dt(anchor)
    if spec is None or start is None:
        return None
    if spec["kind"] == "interval":
        nxt = start + spec["delta"]
        while nxt <= now:
            nxt += spec["delta"]
        return nxt
    days = spec["days"]  # day-of-week set
    for i in range(8):
        cand = _at_time_of(now + timedelta(days=i), start)
        if cand > now and cand.weekday() in days:
            return cand
    return None


def first_occurrence(recurrence: str | None, when: str | None, now: datetime) -> datetime | None:
    """The first run time for a freshly-scheduled task. Intervals honour `when`
    as-is; day-of-week recurrences snap forward to the next matching weekday at
    `when`'s time-of-day (so 'weekdays 5am' scheduled on a Saturday starts Monday)."""
    start = _parse_dt(when)
    if start is None:
        return None
    spec = parse_recurrence(recurrence)
    if spec is None or spec["kind"] == "interval":
        return start
    days = spec["days"]
    base = start if start > now else _at_time_of(now, start)
    for i in range(8):
        cand = base + timedelta(days=i)
        if cand > now and cand.weekday() in days:
            return cand
    return start


class Scheduler:
    """Polls the store for due SCHEDULED tasks and fires them (deterministic)."""

    def __init__(self, store, fire, interval: float = 30.0) -> None:
        self._store = store
        self._fire = fire  # async (task_id) -> None
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
            if (
                t.status == TaskStatus.SCHEDULED
                and not getattr(t, "archived", False)  # archived = put away, don't fire
                and is_due(t.scheduled_for, now)
            ):
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
