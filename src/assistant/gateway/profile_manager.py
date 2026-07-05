"""ProfileManager — N isolated profile runtimes in one process (§4.1).

A profile is a named, colour-coded runtime: one ``Gateway`` + one ``TaskService``
+ its own channels, all alive simultaneously so background tasks in a non-viewed
profile keep running. This module boots them all at server start, runs the
one-time legacy migration first, and owns the create / archive / reload lifecycle.

Isolation is structural (directory-per-profile), not query discipline: each
runtime is constructed from a **derived config** (``Config.with_profile(meta)``)
so every profile-owned path lands under the profile dir.
"""

import asyncio
import os
from collections.abc import Callable, Iterator

from assistant import profiles
from assistant.config import Config, load_config
from assistant.gateway.core import Gateway, build_gateway
from assistant.gateway.migration import migrate_if_needed
from assistant.observability import setup_logging
from assistant.profiles import ProfileMeta
from assistant.settings import Settings

# Platform → env vars that must ALL be present for its channel to run (mirrors the
# migration map; a channel starts iff the profile enables it AND its tokens exist).
_CHANNEL_TOKENS = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "discord": ("DISCORD_BOT_TOKEN",),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
}

# LLM keys that, when set explicitly via env, win over a profile's settings.json
# overlay (env keeps its stronger precedence, §4.1). Maps the config field to its env.
_LLM_ENV = {"provider": "AG2ASSISTANT_LLM_PROVIDER", "model": "AG2ASSISTANT_MODEL"}


class UnknownProfile(Exception):
    """The profile id is not in the registry (WP4 maps to 404)."""


class ArchivedProfile(Exception):
    """The profile is registered but archived (WP4 maps to 410)."""


def config_factory(pid: str) -> Callable[[], Config]:
    """Return a callable that resolves the derived config for profile ``pid`` fresh on
    every call (§4.1).

    On EACH call it: ``load_config()`` (root config, no settings overlay) → re-reads
    the profile's ``ProfileMeta`` from the registry (never a captured snapshot, so
    workspace edits are picked up) → ``with_profile(meta)`` → overlays that profile's
    ``settings.json`` llm provider/model, SKIPPING any key set explicitly via env
    (env wins).
    """

    def resolve() -> Config:
        cfg = load_config()
        meta = profiles.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        cfg = cfg.with_profile(meta)
        _overlay_settings_llm(cfg)
        return cfg

    return resolve


def resolve_active_profile(pid: str | None = None) -> tuple[str, Config, Callable[[], Config]]:
    """Resolve a profile for the CLI ``chat`` path (item 6): its id, derived config, and
    config factory (shared with runtimes).

    ``pid`` defaults to the registry's ``active_default``. Raises ``UnknownProfile`` with
    §3.5 guidance when there is no target (zero profiles / bad id) so callers can print a
    clear message pointing at ``serve`` / browser onboarding / ``profiles create``.
    """
    if pid is None:
        pid = profiles.load_registry().get("active_default")
    meta = profiles.get_profile(pid) if pid else None
    if meta is None:
        raise UnknownProfile(
            "no profile to use — create one first with 'ag2-assistant profiles create "
            "<name>' or run 'ag2-assistant run' and onboard in the browser"
        )
    if meta.archived:
        raise ArchivedProfile(pid)
    factory = config_factory(pid)
    return pid, factory(), factory


def _overlay_settings_llm(cfg: Config) -> None:
    """Layer this profile's settings.json {provider, model} onto ``cfg.llm`` in place,
    skipping any key explicitly set via env (env wins — checked against os.environ)."""
    llm = Settings(cfg.data_dir / "settings.json").get_llm()
    provider = llm.get("provider")
    model = llm.get("model")
    if provider and not os.environ.get(_LLM_ENV["provider"]):
        cfg.llm.provider = provider
    if model and not os.environ.get(_LLM_ENV["model"]):
        cfg.llm.model = model


class ProfileRuntime:
    """One profile's live runtime: gateway + task service + channels + logger."""

    def __init__(self, meta: ProfileMeta, *, memory: bool = True, persist: bool = True) -> None:
        self.meta = meta
        self._memory = memory
        self._persist = persist
        self._config: Config | None = None
        self.gateway: Gateway | None = None
        self.tasks = None
        self.channels: list = []
        self.channel_conflicts: list[str] = []
        # This profile's own HITL registry (permission/question prompts). Its request
        # ids are globally unique, so the global /hitl/{id} dispatcher (app.py) can
        # find the right profile by asking each runtime's registry in turn (§4.1).
        from assistant.hitl import HitlServer

        self.hitl = HitlServer()
        from assistant.observability import profile_logger

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
        gateway's config so a reload (workspace/model edit) is reflected everywhere
        the routes read ``runtime.config`` — before start it's the prepared config."""
        if self.gateway is not None:
            return self.gateway.config
        return self._config

    def refresh_meta(self) -> None:
        """Re-read this profile's registry entry (after a rename/palette/workspace edit)."""
        meta = profiles.get_profile(self.pid)
        if meta is not None:
            self.meta = meta

    def on_close(self, callback: Callable) -> None:
        """Register a callback fired when this runtime is archived/closed (WP4 wires
        its WS handlers here to close sockets with code 4001)."""
        self._close_callbacks.append(callback)

    async def start(self, *, started_channels: dict | None = None) -> None:
        """Build the derived config, construct + start gateway and task service the same
        way the base wiring does, then start this profile's enabled channels.

        ``started_channels`` (platform → owning pid) is the ProfileManager's shared
        first-wins ledger; a conflict is logged + recorded and the channel skipped.
        """
        factory = config_factory(self.pid)
        self._config = factory()

        # Same composition as build_gateway, but with a prepared config + shared factory
        # so reload() re-resolves the profile's config (not the global root).
        self.gateway, self.tasks = build_gateway(
            self._config,
            memory=self._memory,
            platform="gateway",
            persist=self._persist,
            config_factory=factory,
        )
        await self.gateway.start()
        self.tasks.set_emitter(self.gateway.emit_event)  # lifecycle → AG2 stream
        await self.tasks.start()  # task tools + scheduler (per-profile lock)

        await self._start_channels(started_channels if started_channels is not None else {})

    async def _start_channels(self, started_channels: dict) -> None:
        """Start each channel this profile's settings enable AND whose tokens exist;
        first profile (registry order) wins a platform, later ones log + record + skip."""
        from assistant.channels import get_channel

        settings = Settings(self.config.data_dir / "settings.json")
        for platform, envs in _CHANNEL_TOKENS.items():
            if not settings.channel_enabled(platform):
                continue
            if not all(os.environ.get(e) for e in envs):
                continue  # enabled but no token → nothing to connect
            owner = started_channels.get(platform)
            if owner is not None:
                msg = (
                    f"channel '{platform}' already bound to profile '{owner}'; "
                    f"skipping for '{self.pid}' (a bot token serves one connection)"
                )
                self.log.error(msg)
                self.channel_conflicts.append(msg)
                continue
            channel = get_channel(platform)
            await channel.start(self.gateway)
            self.channels.append(channel)
            started_channels[platform] = self.pid
            self.log.info("channel '%s' started for profile '%s'", platform, self.pid)

    async def close(self) -> None:
        """Stop channels, close tasks, close gateway. Idempotent-ish (safe to call once)."""
        self.closing.set()
        for cb in list(self._close_callbacks):
            try:
                res = cb()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("profile close callback", exc, profile=self.pid)
        for ch in list(self.channels):
            try:
                await ch.stop()
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("channel stop", exc, profile=self.pid)
        self.channels.clear()
        if self.tasks is not None:
            await self.tasks.close()
        if self.gateway is not None:
            await self.gateway.close()


class ProfileManager:
    """Boots and owns every profile runtime (§4.1)."""

    def __init__(self, *, memory: bool = True, persist: bool = True) -> None:
        self._memory = memory
        self._persist = persist
        self._runtimes: dict[str, ProfileRuntime] = {}
        # platform → owning pid; the first-wins channel ledger, shared across runtimes.
        self._started_channels: dict[str, str] = {}

    async def start(self) -> None:
        """Run migration first, then boot every UNARCHIVED registered profile.

        Zero profiles is a legal no-op (fresh install, §3.5). Logging is set up once
        against the root config here so per-profile loggers write to the shared file.
        """
        setup_logging(load_config())
        migrate_if_needed()
        for meta in profiles.list_profiles(include_archived=False):
            await self._boot(meta)

    async def _boot(self, meta: ProfileMeta) -> ProfileRuntime:
        runtime = ProfileRuntime(meta, memory=self._memory, persist=self._persist)
        await runtime.start(started_channels=self._started_channels)
        self._runtimes[meta.id] = runtime
        return runtime

    async def close(self) -> None:
        """Close all running runtimes."""
        for runtime in list(self._runtimes.values()):
            await runtime.close()
        self._runtimes.clear()
        self._started_channels.clear()

    def get(self, pid: str) -> ProfileRuntime:
        """Registry-first lookup (§4.1): unknown → UnknownProfile; archived →
        ArchivedProfile; registered+unarchived but not running → RuntimeError (a server
        bug, never lazy-boot)."""
        meta = profiles.get_profile(pid)
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

    @property
    def active_default(self) -> str | None:
        return profiles.load_registry().get("active_default")

    async def create(self, name: str, palette: str, workspace: str | None = None) -> ProfileRuntime:
        """Create a profile (registry + dir) and boot its runtime live (§3.5)."""
        meta = profiles.create_profile(name, palette, workspace=workspace)
        profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
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
        meta = profiles.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        if meta.archived:
            raise ArchivedProfile(pid)

        unarchived = profiles.list_profiles(include_archived=False)
        if len(unarchived) <= 1:
            raise ValueError("cannot archive the last unarchived profile")

        if profiles.load_registry().get("active_default") == pid:
            if not new_default:
                raise ValueError(
                    "archiving the active default requires a replacement (new_default)"
                )
            replacement = profiles.get_profile(new_default)
            if replacement is None or replacement.archived:
                raise ValueError(
                    f"new_default '{new_default}' is not an existing unarchived profile"
                )
            profiles.set_active_default(new_default)

        runtime = self._runtimes.get(pid)
        if runtime is not None:
            # Cascade-cancel in-flight tasks so they land CANCELLED (not limbo), then
            # close (stops scheduler + channels + fires WS-close callbacks).
            if runtime.tasks is not None:
                try:
                    await runtime.tasks.cancel_all(reason="profile-archived")
                except Exception as exc:
                    from assistant.observability import log_suppressed

                    log_suppressed("archive cancel_all", exc, profile=pid)
            await runtime.close()
            self._runtimes.pop(pid, None)
            # Free any channels this profile owned so a restart / other profile can bind.
            for platform, owner in list(self._started_channels.items()):
                if owner == pid:
                    self._started_channels.pop(platform, None)

        profiles.archive_profile(pid)
