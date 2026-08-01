"""ProfileManager — N isolated profile runtimes in one process (§4.1).

A profile is a named, colour-coded runtime: one ``Gateway`` + one ``TaskService``,
all alive simultaneously so background tasks in a non-viewed profile keep running.
This module boots them all at server start and owns the create / archive / reload
lifecycle.

Channels are NOT part of a runtime (ADR 0022): one adapter per registered Connection
starts here on the manager, at install level, and routes each inbound message to a runtime
through the shared ``ChannelRouter``. Which runtime that is comes from the
Channel's **default profile** in the registry, resolved per message.

Isolation is structural (directory-per-profile), not query discipline: each
runtime is constructed from a **derived config** (``Config.with_profile(meta)``)
so every profile-owned path lands under the profile dir.
"""

import asyncio
import shutil
from collections.abc import Callable, Iterable, Iterator, Mapping

from assistant import channels, profiles
from assistant.config import Config, load_config, resolve_config
from assistant.connections import ConnectionStore
from assistant.gateway.core import Gateway, build_gateway
from assistant.hitl import HitlServer
from assistant.observability import log_suppressed, profile_logger, setup_logging
from assistant.paths import Paths
from assistant.profiles import ProfileMeta, ProfileRegistry


def _scrub_tokens(msg: str, values: Iterable[str]) -> str:
    """Replace any of the given token values appearing in ``msg`` with a mask —
    platform libraries embed the raw token in some error messages."""
    for value in values:
        if value:
            msg = msg.replace(value, "•••")
    return msg


class UnknownProfile(Exception):
    """The profile id is not in the registry (WP4 maps to 404)."""


class ArchivedProfile(Exception):
    """The profile is registered but archived (WP4 maps to 410)."""


def config_factory(
    pid: str, paths: Paths, env: Mapping[str, str] | None = None
) -> Callable[[], Config]:
    """Return a callable that resolves the derived config for profile ``pid`` fresh on
    every call (§4.1).

    On EACH call it: ``resolve_config(env, paths)`` (which already derives the install-wide
    active ``llm_configs`` entry onto ``cfg.llm``) → re-reads the profile's ``ProfileMeta``
    from the registry (never a captured snapshot, so rename/accent edits are picked up) →
    ``with_profile(meta)``. The LLM is common across profiles now, so there is no
    per-profile settings overlay — a config change reloads every runtime.
    """

    def resolve() -> Config:
        cfg = resolve_config(env or {}, paths)
        meta = ProfileRegistry(paths).get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        return cfg.with_profile(meta, env=env)

    return resolve


def resolve_active_profile(
    pid: str | None = None, *, paths: Paths, env: Mapping[str, str] | None = None
) -> tuple[str, Config, Callable[[], Config]]:
    """Resolve a profile for the CLI ``chat`` path (item 6): its id, derived config, and
    config factory (shared with runtimes).

    ``pid`` defaults to the registry's ``active_default``. Raises ``UnknownProfile`` with
    §3.5 guidance when there is no target (zero profiles / bad id) so callers can print a
    clear message pointing at ``serve`` / browser onboarding / ``profiles create``.
    """
    registry = ProfileRegistry(paths)
    if pid is None:
        pid = registry.load_registry().get("active_default")
    meta = registry.get_profile(pid) if pid else None
    if meta is None:
        raise UnknownProfile(
            "no profile to use — create one first with 'ag2-assistant profiles create "
            "<name>' or run 'ag2-assistant run' and onboard in the browser"
        )
    if meta.archived:
        raise ArchivedProfile(pid)
    factory = config_factory(pid, paths, env)
    return pid, factory(), factory


class ProfileRuntime:
    """One profile's live runtime: gateway + task service + logger."""

    def __init__(
        self,
        meta: ProfileMeta,
        paths: Paths,
        *,
        env: Mapping[str, str] | None = None,
        memory: bool = True,
        persist: bool = True,
        notifier: Callable | None = None,
        mirror: Callable | None = None,
        questions=None,
        agent_factory: Callable | None = None,
        title_factory: Callable | None = None,
        summary_factory: Callable | None = None,
    ) -> None:
        self.meta = meta
        self.paths = paths
        self._registry = ProfileRegistry(paths)
        self._env = env
        self._memory = memory
        self._persist = persist
        # Collaborators the gateway builds rather than imports: the turn agent and the
        # two cheap-model helpers (chat titles, run summaries). None means production.
        self._agent_factory = agent_factory
        self._title_factory = title_factory
        self._summary_factory = summary_factory
        self._config: Config | None = None
        self.gateway: Gateway | None = None
        self.tasks = None
        # ``(platform, chat_id, text)`` push for task-run outcomes. Channels are
        # install-level, so this reaches out to whoever owns them (the manager).
        self._notifier = notifier
        # Where this profile's completed turns go so an Attached Peer sees them
        # (ADR 0020) — the install's one router, which owns the Peers.
        self._mirror = mirror
        # Where this profile's questions go so an Attached Peer can answer them too —
        # the same router, which knows which Peer that is.
        self._questions = questions
        # This profile's own HITL registry (permission/question prompts). Its request
        # ids are globally unique, so the global /hitl/{id} dispatcher (app.py) can
        # find the right profile by asking each runtime's registry in turn (§4.1).
        self.hitl = HitlServer()
        self.log = profile_logger(meta.id)
        # Close callbacks WP4's WS handlers subscribe to; fired on archive so open
        # sockets get closed with 4001. Also an Event peers can await.
        self.closing = asyncio.Event()
        self._close_callbacks: list = []

    @property
    def pid(self) -> str:
        return self.meta.id

    @property
    def config(self) -> Config | None:
        """The runtime's live config. Once the gateway is up this delegates to the
        gateway's config so a reload (model/config edit) is reflected everywhere
        the routes read ``runtime.config`` — before start it's the prepared config."""
        if self.gateway is not None:
            return self.gateway.config
        return self._config

    def refresh_meta(self) -> None:
        """Re-read this profile's registry entry (after a rename/accent edit)."""
        meta = self._registry.get_profile(self.pid)
        if meta is not None:
            self.meta = meta

    def on_close(self, callback: Callable) -> None:
        """Register a callback fired when this runtime is archived/closed (WP4 wires
        its WS handlers here to close sockets with code 4001)."""
        self._close_callbacks.append(callback)

    async def notify_channel(self, connection: str, chat_id: str, text: str) -> None:
        """Push a message to a chat on a Connection — the task service delivers run
        outcomes through here. The Channel is install-level, so the push is handed to
        the notifier this runtime was built with."""
        if self._notifier is None:
            raise RuntimeError(f"connection {connection!r} is not reachable from this runtime")
        await self._notifier(connection, chat_id, text)

    async def start(self) -> None:
        """Build the derived config, construct + start gateway and task service the same
        way the base wiring does. Channels are not started here — they are install-level
        and owned by the ProfileManager (ADR 0022).
        """
        factory = config_factory(self.pid, self.paths, self._env)
        self._config = factory()

        # Same composition as build_gateway, but with a prepared config + shared factory
        # so reload() re-resolves the profile's config (not the global root).
        self.gateway, self.tasks = build_gateway(
            self._config,
            memory=self._memory,
            platform="gateway",
            persist=self._persist,
            config_factory=factory,
            agent_factory=self._agent_factory,
            title_factory=self._title_factory,
            summary_factory=self._summary_factory,
        )
        await self.gateway.start()
        self.gateway.set_mirror(self._mirror)  # completed turns -> the Attached Peer
        self.gateway.set_question_mirror(self._questions)  # questions -> the same Peer
        self.tasks.set_emitter(self.gateway.emit_event)  # lifecycle → AG2 stream
        self.tasks.set_gateway(self.gateway)  # turns/stops/stream deletion for runs
        self.tasks.set_notifier(self.notify_channel)  # run outcomes -> the origin channel
        await self.tasks.start()  # task tools + scheduler (per-profile lock)

    async def close(self) -> None:
        """Close tasks and gateway. Idempotent-ish (safe to call once). Channels are
        install-level and outlive any one runtime — the manager stops those."""
        self.closing.set()
        for cb in list(self._close_callbacks):
            try:
                res = cb()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                log_suppressed("profile close callback", exc, profile=self.pid)
        if self.tasks is not None:
            await self.tasks.close()
        if self.gateway is not None:
            await self.gateway.close()


class ProfileManager:
    """Boots and owns every profile runtime (§4.1)."""

    def __init__(
        self,
        paths: Paths | None = None,
        config: Config | None = None,
        *,
        env: Mapping[str, str] | None = None,
        memory: bool = True,
        persist: bool = True,
        agent_factory: Callable | None = None,
        channel_factory: Callable | None = None,
        title_factory: Callable | None = None,
        summary_factory: Callable | None = None,
    ) -> None:
        # Install-level layout + config; the pair always agrees. Given neither, both come
        # from the entry-point boundary so ``serve`` needs no wiring; given ``paths``, the
        # config is resolved over that layout. Profile configs are derived per runtime.
        if config is None:
            config = resolve_config(env or {}, paths) if paths is not None else load_config()
        self.config = config
        self.paths = paths if paths is not None else config.paths
        # The environment a runtime's config is re-resolved against on reload. The entry
        # point passes os.environ; anything else stays with the config.yaml layer only.
        self._env = env
        self._registry = ProfileRegistry(self.paths)
        self._connections = ConnectionStore(self.paths, env)
        self._memory = memory
        self._persist = persist
        self._agent_factory = agent_factory
        self._channel_factory = channel_factory or channels.get_channel
        self._title_factory = title_factory
        self._summary_factory = summary_factory
        self._runtimes: dict[str, ProfileRuntime] = {}
        # Connection id → the live install-level adapter (ADR 0022). One per registered
        # Connection, so two bots of one platform run side by side.
        self.channels: dict[str, channels.Channel] = {}
        # Connection id → last start-failure message (bad/missing token, network).
        # Install-level; cleared on that Connection's successful start or stop.
        self.channel_errors: dict[str, str] = {}
        # pid → why that profile's runtime failed to boot at start(). A broken profile
        # must not take the whole server down; the rest boot and this records the reason.
        self.boot_errors: dict[str, str] = {}
        # The one router every adapter is handed. It resolves the profile per inbound
        # message through the directory methods below, so changing a Channel's default
        # profile — or a Peer's own selection — needs no restart.
        self.router = channels.ChannelRouter(self, self.paths)

    # ---- the router's ProfileDirectory (read fresh on every message) ----

    def available_profiles(self, surface: str) -> tuple[channels.AvailableProfile, ...]:
        """Every profile a conversation on ``surface`` could be pointed at right now —
        the running runtimes, named as their user sees them, minus the ones withdrawn
        from that surface. Exposure is read from the registry per message, so a
        withdrawal takes effect without a restart."""
        withdrawn = self._registry.withdrawn_from(surface)
        return tuple(
            channels.AvailableProfile(r.meta.id, r.meta.name)
            for r in self._runtimes.values()
            if r.meta.id not in withdrawn
        )

    def default_profile(self, connection: str) -> str | None:
        """The Connection's default profile, or None when it has none or the profile it
        names is not running."""
        pid = self._registry.connection_defaults().get(connection)
        return pid if pid in self._runtimes else None

    def gateway_for_profile(self, pid: str) -> Gateway | None:
        """The running gateway for a profile id, or None when it is not running."""
        runtime = self._runtimes.get(pid)
        return runtime.gateway if runtime is not None else None

    @property
    def env(self) -> Mapping[str, str]:
        """The ambient environment this install was wired with (``os.environ`` from the
        entry point, empty otherwise) — the only environment the HTTP layer reads."""
        return self._env if self._env is not None else {}

    async def start(self) -> None:
        """Boot every UNARCHIVED registered profile, then start every registered
        Connection.

        Zero profiles is a legal no-op (fresh install, §3.5). Logging is set up once
        against the root config here so per-profile loggers write to the shared file.
        A profile whose boot raises is recorded in ``boot_errors`` and skipped — one
        broken profile must not keep the others (or the server) down.
        """
        setup_logging(self.config)
        for meta in self._registry.list_profiles(include_archived=False):
            try:
                await self._boot(meta)
            except Exception as exc:
                self.boot_errors[meta.id] = str(exc)
                profile_logger(meta.id).error("profile failed to boot: %s", exc)
        for connection in self._connections.list_connections():
            await self.start_channel(connection.id)

    async def _boot(self, meta: ProfileMeta) -> ProfileRuntime:
        runtime = ProfileRuntime(
            meta,
            self.paths,
            env=self._env,
            memory=self._memory,
            persist=self._persist,
            notifier=self.router.push,
            mirror=self.router.mirror,
            questions=self.router,
            agent_factory=self._agent_factory,
            title_factory=self._title_factory,
            summary_factory=self._summary_factory,
        )
        await runtime.start()
        self.boot_errors.pop(meta.id, None)
        self._runtimes[meta.id] = runtime
        return runtime

    def _channel(self, connection: str) -> channels.Channel:
        """The live adapter for a Connection, or a refusal to reach it."""
        channel = self.channels.get(connection)
        if channel is None:
            raise RuntimeError(f"connection {connection!r} is not running")
        return channel

    async def notify_channel(self, connection: str, chat_id: str, text: str) -> None:
        """Push a message into a chat through the Connection it belongs to — task-run
        outcomes are delivered this way."""
        await self._channel(connection).notify(chat_id, text)

    async def ask_channel(
        self, connection: str, chat_id: str, inquiry: str, question: channels.Choose
    ) -> None:
        """Show a question with its options in a chat on that Connection (ADR 0020)."""
        await self._channel(connection).ask(chat_id, inquiry, question)

    async def retract_channel(self, connection: str, chat_id: str, inquiry: str) -> None:
        """Take back a question shown on that Connection — it has been resolved."""
        await self._channel(connection).retract(chat_id, inquiry)

    async def start_channel(self, cid: str) -> tuple[bool, str | None]:
        """Start the Connection ``cid`` on the tokens it holds, handing the adapter the
        shared router. Returns ``(active, reason)``; a failure records it and stays down."""
        log = profile_logger("default")
        if cid in self.channels:
            return True, None
        connection = self._connections.get_connection(cid)
        if connection is None:
            raise ValueError(f"unknown connection: {cid}")
        platform = connection.platform
        envs = profiles.CHANNEL_TOKEN_ENVS[platform]
        # The Connection's tokens are re-read here, not taken from a boot-time snapshot:
        # a token saved or cleared mid-session must apply to this very start.
        tokens = self._connections.tokens_for(cid)
        if not all(tokens.get(e) for e in envs):
            msg = f"no token configured for {platform}"
            self.channel_errors[cid] = msg
            return False, msg
        # start() and get_channel both raise on a bad token / network failure; that must
        # not propagate — it would 500 the endpoint and crash boot.
        try:
            channel = self._channel_factory(
                platform,
                connection=cid,
                **{channels.TOKEN_ARGS[e]: tokens[e] for e in envs},
            )
            await channel.start(self.router)
        except Exception as exc:
            # Platform libraries embed the raw token in some error messages
            # (e.g. Telegram's "The token <value> was rejected"); scrub it —
            # this string is logged AND returned via GET /api/connections.
            msg = _scrub_tokens(f"could not start '{platform}': {exc}", tokens.values())
            log.error(msg)
            self.channel_errors[cid] = msg
            return False, msg
        self.channels[cid] = channel
        self.channel_errors.pop(cid, None)
        log.info("connection '%s' (%s) started", cid, platform)
        return True, None

    async def stop_channel(self, cid: str) -> bool:
        """Stop the Connection ``cid`` if it is live. Returns True if one was stopped."""
        channel = self.channels.pop(cid, None)
        if channel is None:
            return False
        try:
            await channel.stop()
        except Exception as exc:
            log_suppressed("channel stop", exc)
        self.channel_errors.pop(cid, None)
        profile_logger("default").info("connection '%s' stopped", cid)
        return True

    async def restart_channel(self, cid: str) -> tuple[bool, str | None]:
        """Re-apply the live state of a Connection after its tokens changed: stop it,
        then start it again if all its tokens are now present (guarded). Returns
        ``(active, reason)`` like ``start_channel``. Unknown id → ``ValueError``."""
        if self._connections.get_connection(cid) is None:
            raise ValueError(f"unknown connection: {cid}")
        await self.stop_channel(cid)
        return await self.start_channel(cid)

    async def close(self) -> None:
        """Stop every Channel, then close all running runtimes."""
        for cid in list(self.channels):
            await self.stop_channel(cid)
        for runtime in list(self._runtimes.values()):
            await runtime.close()
        self._runtimes.clear()
        self.channel_errors.clear()

    def get(self, pid: str) -> ProfileRuntime:
        """Registry-first lookup (§4.1): unknown → UnknownProfile; archived →
        ArchivedProfile; registered+unarchived but not running → RuntimeError (a server
        bug, never lazy-boot)."""
        meta = self._registry.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        if meta.archived:
            raise ArchivedProfile(pid)
        runtime = self._runtimes.get(pid)
        if runtime is None:
            raise RuntimeError(f"profile '{pid}' is registered and unarchived but not running")
        return runtime

    def runtimes(self) -> Iterator[ProfileRuntime]:
        """Iterate the running runtimes (registry order not guaranteed)."""
        return iter(self._runtimes.values())

    def runtimes_by_id(self) -> dict[str, ProfileRuntime]:
        """The running runtimes keyed by pid (a copy; safe to read without raising for
        an absent/archived pid, unlike ``get``)."""
        return dict(self._runtimes)

    @property
    def active_default(self) -> str | None:
        return self._registry.load_registry().get("active_default")

    async def create(self, name: str, accent: str) -> ProfileRuntime:
        """Create a profile (registry + dir) and boot its runtime live (§3.5).
        ``accent`` is a ``#rrggbb`` hex (ADR 0002)."""
        meta = self._registry.create_profile(name, accent)
        self._registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
        return await self._boot(meta)

    async def reload(self, pid: str) -> None:
        """Reference-swap reload of one profile's runtime (gateway.reload also reloads
        its task service via the shared config factory)."""
        runtime = self.get(pid)
        runtime.refresh_meta()
        await runtime.gateway.reload()

    async def archive(self, pid: str, new_default: str | None = None) -> None:
        """Archive a profile with the §4.9 guardrails.

        - Refuse the last unarchived profile (ValueError).
        - If ``pid`` is the active_default, ``new_default`` is required and must name an
          existing, unarchived profile (ValueError otherwise); it becomes active first.
        - Then stop scheduler/tasks (cascade-cancelling in-flight tasks to CANCELLED),
          fire the runtime's close callbacks (WP4 closes WS with 4001), mark archived in
          the registry, and drop + close the runtime.
        """
        meta = self._registry.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        if meta.archived:
            raise ArchivedProfile(pid)

        unarchived = self._registry.list_profiles(include_archived=False)
        if len(unarchived) <= 1:
            raise ValueError("cannot archive the last unarchived profile")

        if self._registry.load_registry().get("active_default") == pid:
            if not new_default:
                raise ValueError(
                    "archiving the active default requires a replacement (new_default)"
                )
            replacement = self._registry.get_profile(new_default)
            if replacement is None or replacement.archived:
                raise ValueError(
                    f"new_default '{new_default}' is not an existing unarchived profile"
                )
            self._registry.set_active_default(new_default)

        # Channels stay up: they are install-level and never owned by a profile. Any
        # that defaulted to this one are cleared in profiles.archive_profile below, and
        # land in the no-default state — the router refuses there rather than routing a
        # message into a profile that is no longer running.
        runtime = self._runtimes.get(pid)
        if runtime is not None:
            # Cascade-cancel in-flight tasks so they land CANCELLED (not limbo), then
            # close (stops the scheduler + fires WS-close callbacks).
            if runtime.tasks is not None:
                try:
                    await runtime.tasks.cancel_all(reason="profile-archived")
                except Exception as exc:
                    log_suppressed("archive cancel_all", exc, profile=pid)
            await runtime.close()
            self._runtimes.pop(pid, None)

        self._registry.archive_profile(pid)

    async def restore(self, pid: str) -> ProfileRuntime:
        """Un-archive a profile and boot its runtime live (§4.9, ADR 0003).

        Symmetric with ``create``: clear the archived flag then ``_boot``. All-or-
        nothing — if boot fails the flag is rolled back to archived so the profile is
        never left in the unarchived-but-not-running state ``get`` treats as a server
        bug. Unknown → UnknownProfile; a live (non-archived) profile → ValueError.
        """
        meta = self._registry.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        if not meta.archived:
            raise ValueError(f"profile is not archived: {pid}")

        restored = self._registry.restore_profile(pid)
        try:
            return await self._boot(restored)
        except Exception:
            # Roll back the flag so the invariant "unarchived ⟺ running" holds.
            self._registry.archive_profile(pid)
            self._runtimes.pop(pid, None)
            raise

    async def purge(self, pid: str) -> None:
        """Permanently delete an ARCHIVED profile: erase its folder and drop its
        registry entry (§4.9, ADR 0003). The only state-destroying operation.

        Archive-first: refuses a live profile (ValueError → 409) so delete never has to
        tear down a running runtime, reassign the active default, or hit the last-profile
        guardrail — an archived profile is already none of those. Unknown → UnknownProfile.
        """
        meta = self._registry.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        if not meta.archived:
            raise ValueError(f"cannot delete a profile that is not archived: {pid}")

        # Archived profiles are never booted, but be defensive if one somehow is.
        runtime = self._runtimes.pop(pid, None)
        if runtime is not None:
            await runtime.close()

        # Erase the folder first; only drop the registry entry once the disk is clear,
        # so a failed rmtree leaves the profile cleanly archived rather than half-gone.
        shutil.rmtree(self._registry.profile_dir(pid), ignore_errors=True)
        self._registry.delete_profile(pid)
