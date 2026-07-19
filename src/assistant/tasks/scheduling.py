"""Task scheduling — one-shot and recurring.

A scheduled task sits in status SCHEDULED with `scheduled_for` (ISO datetime) and,
for recurring jobs, a `recurrence` cron expression. A deterministic poll loop
(`Scheduler`, no LLM) checks for due tasks and fires a callback:

- one-shot  → the task itself runs, then follows the normal lifecycle.
- recurring → a fresh run is spawned for this occurrence and the template is
  re-armed for the next one (so each run is its own task in the history).

Recurrence is standard 5-field cron (minute hour day-of-month month day-of-week,
POSIX/Vixie syntax, evaluated by cronsim) plus the standard @nicknames
(@hourly/@daily/@weekly/@monthly/@yearly). Occurrences are wall-clock in the
local timezone, so "0 4-14 * * 1-5" is hourly 04:00–14:00 on weekdays.
"""

import asyncio
import contextlib
from datetime import datetime, timedelta

from cron_descriptor import ExpressionDescriptor, Options
from cronsim import CronSim, CronSimError

from assistant.observability import log_suppressed
from assistant.tasks.model import TaskStatus

# Standard Vixie-cron nicknames (cronsim itself only takes 5-field expressions).
_NICKNAMES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}


def normalize_cron(spec: str | None) -> str | None:
    """Canonical 5-field cron expression for a recurrence spec (expanding
    @nicknames), or None if it isn't a valid cron schedule."""
    if not spec:
        return None
    s = _NICKNAMES.get(spec.strip().lower(), spec.strip())
    try:
        CronSim(s, datetime.now().astimezone())
    except CronSimError:
        return None
    return s


def describe_cron(spec: str | None) -> str | None:
    """Human-readable description of a cron recurrence (e.g. "Between 04:00 and
    14:59, Monday through Friday"), or None if invalid. Uses cron-descriptor
    (the CronExpressionDescriptor/cronstrue family) rather than cronsim's
    explain() — its phrasing reads far more naturally."""
    expr = normalize_cron(spec)
    if expr is None:
        return None
    opts = Options()
    opts.use_24hour_time_format = True
    try:
        desc = ExpressionDescriptor(expr, opts).get_description()
    except Exception:
        return CronSim(expr, datetime.now().astimezone()).explain()  # cronsim-valid fallback
    # cron-descriptor drops the cadence for minute-0 + hour-range ("0 4-14 …" →
    # "Between 04:00 and 14:59, …"), unlike its JS sibling cronstrue ("Every
    # hour, between 04:00 AM and 02:59 PM, …"). Restore the canonical phrasing.
    if desc.startswith("Between "):
        desc = "Every hour, b" + desc[1:]
    return desc


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    # Honour an explicit timezone as-is (so schedules don't drift by server tz);
    # only naive strings are assumed to be in the local timezone.
    return dt if dt.tzinfo else dt.astimezone()


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
    if r and r != "off" and normalize_cron(r) is None:
        return (
            f"Couldn't parse '{recurrence}' as a recurrence. Use standard 5-field cron "
            "(minute hour day-of-month month day-of-week) — e.g. '0 9 * * *' = daily "
            "09:00, '0 4-14 * * 1-5' = hourly 04:00–14:00 on weekdays — or a nickname "
            "(@hourly/@daily/@weekly/@monthly)."
        )
    return None


def next_occurrence(recurrence: str | None, now: datetime) -> datetime | None:
    """The next occurrence strictly after `now`, or None if not recurring/valid."""
    expr = normalize_cron(recurrence)
    if expr is None:
        return None
    return next(CronSim(expr, now))


def first_occurrence(recurrence: str | None, when: str | None) -> datetime | None:
    """The first run time for a freshly-scheduled task. One-offs honour `when`
    as-is; recurring tasks snap to the first cron match at/after `when`, so a
    weekday-only cron scheduled on a Saturday starts Monday and `when` acts as
    a not-before floor. A past `when` yields a past match — i.e. due now —
    and the re-arm (`next_occurrence` from now) skips the missed slots."""
    start = _parse_dt(when)
    if start is None:
        return None
    expr = normalize_cron(recurrence)
    if expr is None:
        return start
    # CronSim iterates strictly after its start; back off a minute so a `when`
    # that lands exactly on a match counts as the first occurrence.
    return next(CronSim(expr, start - timedelta(minutes=1)))


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
            except Exception as exc:
                log_suppressed("scheduler tick", exc)
                # A bad record must never kill the loop.

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
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
