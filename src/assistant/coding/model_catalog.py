"""List the models an ACP adapter offers, for the Settings model picker.

Both adapters report their catalog in the ACP ``session/new`` response, in two
shapes (verified live):

  - codex-acp 1.1.7: the legacy ``models`` field — ``availableModels`` entries
    of modelId + human name + description, ids like ``gpt-5.6-sol[medium]``
    (model plus reasoning effort).
  - claude-agent-acp 0.55.0: the stable ``configOptions`` mechanism — the
    ``category == "model"`` select, values like ``opus[1m]``/``sonnet`` (the
    bracket is part of the model preference, e.g. 1M context — NOT an effort).

Listing costs no tokens: the probe spawns the adapter, runs ``initialize`` +
``session/new``, reads the catalog and kills the subprocess — no prompt is ever
sent. It is not free of side effects, though: ``session/new`` creates a real
agent session, so the CLI may leave a session record behind (codex writes one
under ``~/.codex/sessions``) — hence the TTL cache, so opening the Settings form
doesn't mint one per render.

Catalog values ride to the adapter verbatim via the env interface in
acp_provider (``ANTHROPIC_MODEL`` / ``CODEX_CONFIG``); this module only feeds
the picker UI. A probe needs to SPAWN the adapter locally, so there is no
catalog in host-bridge (Docker) mode even though the entry is usable there —
:func:`unavailable_reason` names that case so the form can say why.
"""

import asyncio
import contextlib
import json
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from assistant.coding import detect

# One adapter spawn round-trip; generous because npx-style launchers cold-start.
PROBE_TIMEOUT = 20.0
# The catalog changes with adapter/CLI releases, not per minute — cache briefly
# so reopening the Settings form doesn't respawn the adapter every time.
CACHE_TTL = 300.0


@dataclass(frozen=True)
class CatalogModel:
    """One entry of an adapter's model catalog."""

    id: str  # value to store/send, e.g. "gpt-5.6-sol[medium]" or "opus[1m]"
    name: str  # human label, e.g. "GPT-5.6-Sol (medium)" or "Opus"
    description: str


def parse(result: dict) -> tuple[list[CatalogModel], str]:
    """The (models, current) out of a ``session/new`` result, tolerantly: an
    unknown/odd shape reads as an empty catalog, never raises.

    A ``"default"`` pseudo-value is dropped from the catalog and normalised to
    ``""`` in ``current``: it is the adapter's "whatever the CLI is configured
    for" row, which for us is an EMPTY model (no model env derived at all) —
    sending the literal string ``default`` as a model name would just fail.
    """
    if not isinstance(result, dict):
        return [], ""
    models = result.get("models")
    if isinstance(models, dict):  # codex-acp's legacy models field
        out = [
            CatalogModel(
                id=str(m.get("modelId") or ""),
                name=str(m.get("name") or ""),
                description=str(m.get("description") or ""),
            )
            for m in models.get("availableModels") or []
            if isinstance(m, dict) and m.get("modelId") and m.get("modelId") != "default"
        ]
        return out, _current(models.get("currentModelId"))
    for opt in result.get("configOptions") or []:  # ACP session config options
        if not isinstance(opt, dict) or opt.get("category") != "model":
            continue
        out = [
            CatalogModel(
                id=str(o.get("value") or ""),
                name=str(o.get("name") or ""),
                description=str(o.get("description") or ""),
            )
            for o in opt.get("options") or []
            if isinstance(o, dict) and o.get("value") and o.get("value") != "default"
        ]
        return out, _current(opt.get("currentValue"))
    return [], ""


def _current(value: object) -> str:
    """The adapter's current selection, with its "default" row read as empty."""
    current = str(value or "")
    return "" if current == "default" else current


class ModelCatalog:
    """The adapter model catalogs for one install, with its own TTL cache.

    Nothing is shared between instances: two catalogs never see each other's cache,
    and where adapters are looked up comes from ``search_path``. ``bridge`` is the
    host ACP bridge (if any) — in that mode there is no local adapter to spawn, so
    :meth:`unavailable_reason` says so instead of probing.
    """

    def __init__(
        self,
        *,
        search_path: Sequence[Path] = (),
        bridge: "detect.BridgeEndpoint | None" = None,
        probe_timeout: float = PROBE_TIMEOUT,
        cache_ttl: float = CACHE_TTL,
        clock=time.monotonic,
    ) -> None:
        self._search_path = search_path
        self._bridge = bridge
        self._probe_timeout = probe_timeout
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, list[CatalogModel], str]] = {}
        # agent -> the probe currently in flight, so concurrent form opens (or a
        # double render) share one adapter spawn instead of racing two.
        self._inflight: dict[str, asyncio.Task] = {}

    async def _probe(self, agent: str) -> tuple[list[CatalogModel], str]:
        """Spawn the adapter and read its catalog. Raises on any transport failure —
        the caller decides how a failure reads (the route maps it to an empty list)."""
        info = detect.resolve_agent(agent, self._search_path)
        if info is None:
            return [], ""
        proc = await asyncio.create_subprocess_exec(
            *info.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            if proc.stdin is None or proc.stdout is None:  # PIPE was asked for above
                raise RuntimeError(f"{agent} adapter exposed no stdio pipes")
            stdin, stdout = proc.stdin, proc.stdout

            async def rpc(rid: int, method: str, params: dict) -> dict:
                req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
                stdin.write((json.dumps(req) + "\n").encode())
                await stdin.drain()
                while True:  # skip notifications the adapter may interleave
                    line = await stdout.readline()
                    if not line:
                        raise RuntimeError(f"{agent} adapter closed the pipe during the probe")
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue  # a launcher banner / stray log line, not JSON-RPC
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("id") == rid:
                        if "error" in msg:
                            raise RuntimeError(str(msg["error"])[:200])
                        return msg.get("result") or {}

            await rpc(
                1,
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
                },
            )
            # cwd is required but irrelevant to the catalog; no MCP servers, no prompt.
            result = await rpc(2, "session/new", {"cwd": tempfile.gettempdir(), "mcpServers": []})
            return parse(result)
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            # Reap the child so no zombie outlives the probe (and short-lived event
            # loops don't warn about an unfinished child watcher).
            with contextlib.suppress(Exception):
                await proc.wait()

    async def _probe_and_cache(self, agent: str) -> tuple[list[CatalogModel], str]:
        """One timed probe plus the cache write — the body shared by concurrent callers."""
        try:
            models, current = await asyncio.wait_for(
                self._probe(agent), timeout=self._probe_timeout
            )
            if models:  # never cache a failure/empty probe — the user may be mid-install
                self._cache[agent] = (self._clock(), models, current)
            return models, current
        finally:
            self._inflight.pop(agent, None)

    async def list_models(
        self, agent: str, refresh: bool = False
    ) -> tuple[list[CatalogModel], str]:
        """The adapter's model catalog and its current default, TTL-cached per agent.

        Empty catalog when the adapter is missing; transport errors propagate (the
        gateway route guards and serves them as an empty list).
        """
        now = self._clock()
        cached = None if refresh else self._cache.get(agent)
        if cached is not None and now - cached[0] < self._cache_ttl:
            return cached[1], cached[2]
        task = self._inflight.get(agent)
        if task is None:
            task = asyncio.ensure_future(self._probe_and_cache(agent))
            # Retrieve the exception even if every awaiter went away (client hung up),
            # so a failed shared probe doesn't log "exception was never retrieved".
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._inflight[agent] = task
        # shield: one caller's cancellation must not kill the probe the others await.
        return await asyncio.shield(task)

    def unavailable_reason(self, agent: str) -> str:
        """Why no probe can run right now, as a stable token for the UI to word:

        - ``"bridge"`` — host-bridge (Docker) mode: the adapter lives on the host,
          out of reach of a local spawn, so there is no catalog to read.
        - ``"adapter_missing"`` — the adapter is not on the search path.
        - ``""`` — a probe is possible.
        """
        try:
            if self._bridge is not None:
                return "bridge"
            if detect.resolve_agent(agent, self._search_path) is not None:
                return ""
            return "adapter_missing"
        except Exception:
            return "adapter_missing"


def as_view(models: list[CatalogModel], current: str, reason: str = "") -> dict:
    """The JSON shape the Settings picker consumes. ``reason`` is empty on success
    and one of :func:`unavailable_reason`'s tokens (or ``"probe_failed"``) when the
    catalog came back empty, so the form can explain itself instead of silently
    degrading to a free-text field."""
    return {"models": [asdict(m) for m in models], "current": current, "reason": reason}
