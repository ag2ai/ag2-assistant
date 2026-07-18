"""Task scheduling — deterministic, no LLM.

A Task carries a schedule union ({kind: manual|once|cron}) and a derived
``next_run_at``. The poll loop (`Scheduler`) scans the store and fires a
callback for every armed, unpaused task whose ``next_run_at`` has arrived;
the service re-arms (cron) or disarms (once) it. Recurrence is standard
5-field cron (POSIX/Vixie, evaluated by cronsim) plus @nicknames; occurrences
are wall-clock in the local timezone.
"""

import asyncio
from datetime import datetime

from cronsim import CronSim, CronSimError

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
    from cron_descriptor import ExpressionDescriptor, Options

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


def parse_dt(iso: str | None) -> datetime | None:
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
    dt = parse_dt(scheduled_for)
    return dt is not None and dt <= now


def next_occurrence(recurrence: str | None, now: datetime) -> datetime | None:
    """The next occurrence strictly after `now`, or None if not recurring/valid."""
    expr = normalize_cron(recurrence)
    if expr is None:
        return None
    return next(CronSim(expr, now))


def compute_next_run(schedule: dict, now: datetime, *, after_fire: bool = False) -> str | None:
    """The next fire time (ISO) a schedule implies, or None (manual / exhausted).

    ``after_fire=True`` is the scheduler's re-arm: a 'once' that just fired is
    exhausted; a cron advances to its next occurrence strictly after `now`.
    """
    from assistant.tasks.model import ScheduleKind

    kind = schedule.get("kind")
    if kind == ScheduleKind.ONCE:
        return None if after_fire else schedule.get("at")
    if kind == ScheduleKind.CRON:
        nxt = next_occurrence(schedule.get("cron"), now)
        return nxt.isoformat() if nxt is not None else None
    return None


def schedule_text(schedule: dict) -> str:
    """Short human description of a schedule union, for lists and tool replies."""
    from assistant.tasks.model import ScheduleKind

    kind = schedule.get("kind")
    if kind == ScheduleKind.ONCE:
        return f"once at {schedule.get('at')}"
    if kind == ScheduleKind.CRON:
        return describe_cron(schedule.get("cron")) or str(schedule.get("cron"))
    return "manual"


class Scheduler:
    """Polls the store for armed, unpaused tasks and fires them (deterministic)."""

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
                from assistant.observability import log_suppressed

                log_suppressed("scheduler tick", exc)
                # A bad record must never kill the loop.

    async def tick(self, now: datetime | None = None) -> list[str]:
        """One scan: fire every armed, unpaused task whose time has come."""
        now = now or datetime.now().astimezone()
        fired = []
        for t in await self._store.list_tasks():
            if not t.paused and t.next_run_at and is_due(t.next_run_at, now):
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
