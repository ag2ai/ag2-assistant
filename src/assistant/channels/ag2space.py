"""AG2 Space channel — the relay's task files in, result and proactive files out.

An AG2 Space cloud instance runs the assistant in a pod next to a relay bridge
(ag2-sparrow) that turns room messages into ``<workspace>/tasks/<id>.txt`` and
delivers ``<workspace>/results/<id>.txt`` back to the room. This adapter is the
assistant's end of that file protocol: a task file becomes an ``InboundMessage``
whose ``chat_id`` is the Matrix room, the outcome becomes the result file, and an
unsolicited message (a task-run outcome) becomes a ``results/proactive-*.txt`` the
bridge posts to the room named on its first line.
"""

import asyncio
import contextlib
import logging
import os
import re
import time
from pathlib import Path

from assistant.channels.base import Channel, InboundMessage
from assistant.channels.router import ChannelRouter, Choose, Outcome, spoken_text

try:
    from ag2_sparrow.local_task_protocol import canonical_access_tier, parse_task_headers_trusted
except ImportError:  # pragma: no cover - the extra is optional
    canonical_access_tier = None
    parse_task_headers_trusted = None

log = logging.getLogger("assistant.channels.ag2space")

PLATFORM = "ag2space"
# A claimed task keeps its id and header block, so the bridge still finds it by id.
CLAIM_SUFFIX = ".claimed-ag2-assistant"
# state/answered/<id>: the task has its result; the claim in tasks/ is not a turn to retry.
ANSWERED_DIR = "answered"
POLL_S = 1.0
# A result body the bridge archives without posting.
NO_SEND = "[no-send]"
GUEST_REPLY = (
    "This assistant only takes instructions from its owner. "
    "Ask them directly, or in a room where they can hand the request on."
)
# The bridge appends prose blocks meant for other engines after the headers.
_BODY_STOPS = ("===SKILL INSTRUCTIONS", "===TEAM", "===GUEST", "===ENGAGE")
# A task id as the bridge names files; dot-files (temp files) never match.
_TID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,120}$")


def task_id(path: Path) -> str:
    """The task id a file in tasks/ carries, with the ``.txt`` or claim suffix dropped."""
    name = path.name
    for suffix in (".txt", CLAIM_SUFFIX):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def answered_marker(state_dir: Path, tid: str) -> Path:
    return state_dir / ANSWERED_DIR / tid


def mark_answered(state_dir: Path, tid: str) -> None:
    """Record on disk that ``tid`` has its result, after the result file is in place."""
    marker = answered_marker(state_dir, tid)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def pending_tasks(tasks_dir: Path, state_dir: Path) -> list[Path]:
    """Unclaimed task files oldest first, plus claims without an answered marker
    (a turn cut off mid-way, retried); an answered claim waits for the bridge."""
    if not tasks_dir.is_dir():
        return []
    found = [
        p
        for p in tasks_dir.iterdir()
        if p.is_file()
        and (p.name.endswith(".txt") or p.name.endswith(CLAIM_SUFFIX))
        and _TID_RE.match(task_id(p))
        and not (p.name.endswith(CLAIM_SUFFIX) and answered_marker(state_dir, task_id(p)).exists())
    ]
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name))


def prune_answered(state_dir: Path, tasks_dir: Path) -> int:
    """Drop the markers of tasks the bridge has archived. Returns how many went."""
    answered = state_dir / ANSWERED_DIR
    if not answered.is_dir():
        return 0
    gone = 0
    for marker in answered.iterdir():
        if not any(tasks_dir.glob(f"{marker.name}.*")):
            with contextlib.suppress(OSError):
                marker.unlink()
                gone += 1
    return gone


def _write_atomic(dest: Path, body: str) -> None:
    """Temp file, fsync, rename: the bridge reads a file the moment it exists."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body.rstrip("\n") + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)


def parse_task(text: str) -> tuple[dict, str]:
    """Headers and body of a task file, by the relay's trusted-writer rule; the body
    stops at the first block meant for another engine."""
    if parse_task_headers_trusted is None:
        raise RuntimeError(
            "the AG2 Space channel needs ag2-sparrow: pip install ag2-assistant[ag2space]"
        )
    parsed = parse_task_headers_trusted(text)
    lines: list[str] = []
    for line in parsed.body.split("\n"):
        if line.startswith(_BODY_STOPS):
            break
        lines.append(line)
    return dict(parsed.headers), "\n".join(lines).strip()


def owner_tier(headers: dict) -> bool:
    if canonical_access_tier is None:
        raise RuntimeError(
            "the AG2 Space channel needs ag2-sparrow: pip install ag2-assistant[ag2space]"
        )
    return canonical_access_tier(headers.get("access_tier")) == "owner"


def render_choose(question: Choose) -> str:
    """A question as text with its options numbered — rooms have no buttons."""
    lines = [question.text, ""]
    lines += [f"{index}. {option.label}" for index, option in enumerate(question.options, 1)]
    return "\n".join(lines)


def render(outcome: Outcome) -> str:
    """The result body an outcome becomes; silence is a result the bridge archives."""
    if isinstance(outcome, Choose):
        return render_choose(outcome)
    spoken = spoken_text(outcome)
    return spoken if spoken else NO_SEND


class AG2SpaceChannel(Channel):
    """One cloud instance's workspace, polled for the bridge's task files."""

    platform = PLATFORM

    def __init__(
        self,
        workspace: str | os.PathLike[str] = "",
        owner: str = "",
        connection: str = "",
        *,
        poll_s: float = POLL_S,
    ) -> None:
        if not workspace:
            raise ValueError(
                "AG2 Space needs the workspace directory; pass workspace= (AG2SPACE_WORKSPACE)."
            )
        if not owner:
            raise ValueError("AG2 Space needs the owner's Matrix id; pass owner= (AG2SPACE_OWNER).")
        if parse_task_headers_trusted is None:
            raise RuntimeError(
                "the AG2 Space channel needs ag2-sparrow: pip install ag2-assistant[ag2space]"
            )
        self.workspace = Path(workspace)
        self.owner = owner
        self.connection = connection
        self._poll_s = poll_s
        self._router: ChannelRouter | None = None
        self._loop_task: asyncio.Task | None = None
        self._seq = 0

    @property
    def tasks_dir(self) -> Path:
        return self.workspace / "tasks"

    @property
    def results_dir(self) -> Path:
        return self.workspace / "results"

    @property
    def state_dir(self) -> Path:
        return self.workspace / "state"

    async def start(self, router: ChannelRouter) -> None:
        self._router = router
        for d in (self.tasks_dir, self.results_dir, self.state_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._loop_task = asyncio.create_task(self._loop(), name="ag2space-poll")

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

    async def _loop(self) -> None:
        while True:
            await self.poll()
            await asyncio.sleep(self._poll_s)

    async def poll(self) -> int:
        """One pass over the workspace: every pending task handled, finished markers
        pruned. Returns how many tasks were handled."""
        handled = 0
        for path in pending_tasks(self.tasks_dir, self.state_dir):
            try:
                await self._handle(path)
                handled += 1
            except Exception as exc:  # one task never stops the loop
                log.error("%s: %s: %s", path.name, type(exc).__name__, exc)
        prune_answered(self.state_dir, self.tasks_dir)
        return handled

    async def _handle(self, path: Path) -> None:
        tid = task_id(path)
        claimed = path if path.name.endswith(CLAIM_SUFFIX) else path.with_name(tid + CLAIM_SUFFIX)
        if path != claimed:
            try:
                path.rename(claimed)
            except OSError:
                return  # the bridge archived it meanwhile
        try:
            headers, body = parse_task(claimed.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return
        if not body:
            self._answer(tid, NO_SEND)
            return
        if not owner_tier(headers):
            log.info("%s: %s message refused", tid, headers.get("access_tier") or "untiered")
            self._answer(tid, GUEST_REPLY)
            return
        outcome = await self._require_router().handle(self.normalize(tid, headers, body))
        self._answer(tid, render(outcome))

    def normalize(self, tid: str, headers: dict, body: str) -> InboundMessage:
        """The task as the router sees it: the room is the chat, the writer the sender.
        The broker only hands this pod tasks addressed to it, so every one is a mention."""
        return InboundMessage(
            text=body,
            sender_id=headers.get("user_id") or self.owner,
            chat_id=headers.get("channel_id") or f"task:{tid}",
            platform=self.platform,
            is_direct=True,
            connection=self.connection,
            mentioned=True,
            sender_name=headers.get("sender_name") or None,
            raw=headers,
        )

    def _answer(self, tid: str, body: str) -> None:
        """The result file for the bridge, then the answered marker for the poll."""
        _write_atomic(self.results_dir / f"{tid}.txt", body)
        mark_answered(self.state_dir, tid)

    async def notify(self, chat_id: str, text: str) -> None:
        """A message no task asked for: a proactive file addressed to the room."""
        self._seq += 1
        name = f"proactive-{int(time.time() * 1000)}-{os.getpid()}-{self._seq}.txt"
        _write_atomic(self.results_dir / name, f"[channel: {chat_id}]\n{text}")

    async def ask(self, chat_id: str, inquiry: str, question: Choose) -> None:
        """A question mirrored into the room as text; a room has no buttons to resolve
        it, so it is answered on the surface that raised it."""
        await self.notify(chat_id, render_choose(question))

    async def retract(self, chat_id: str, inquiry: str) -> None:
        """Nothing to take back: a posted message stays posted."""
