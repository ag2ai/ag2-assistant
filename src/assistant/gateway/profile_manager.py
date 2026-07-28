"""ProfileManager — N isolated profile runtimes in one process (§4.1).

A profile is a named, colour-coded runtime: one ``Gateway`` + one ``TaskService``,
all alive simultaneously so background tasks in a non-viewed profile keep running.
This module boots them all at server start and owns the create / archive / reload
lifecycle.

Channels are NOT part of a runtime (ADR 0019): each adapter starts once for the
whole install, here on the manager, and routes each inbound message to a runtime
through the shared ``ChannelRouter``. Which runtime that is comes from the
Channel's **default profile** in the registry, resolved per message.

Isolation is structural (directory-per-profile), not query discipline: each
runtime is constructed from a **derived config** (``Config.with_profile(meta)``)
so every profile-owned path lands under the profile dir.
"""

import asyncio
import os
import shutil
from collections.abc import Callable, Iterator

from assistant import channels, profiles
from assistant.config import Config, load_config
from assistant.gateway.core import Gateway, build_gateway
from assistant.hitl import HitlServer
from assistant.observability import log_suppressed, profile_logger, setup_logging
from assistant.profiles import ProfileMeta

# Platform → env vars that must ALL be present for its channel to run. Canonical
# home is ``profiles`` (dependency-light, so both this module and the secrets store
# import it without a cycle); re-exported here under the name existing code uses.
_CHANNEL_TOKENS = profiles.CHANNEL_TOKEN_ENVS


def _scrub_tokens(msg: str, envs: tuple[str, ...]) -> str:
    """Replace any of the given env vars' current values appearing in ``msg`` with a
    mask — platform libraries embed the raw token in some error messages."""
    for env in envs:
        value = os.environ.get(env)
        if value:
            msg = msg.replace(value, "•••")
    return msg


class UnknownProfile(Exception):
    """The profile id is not in the registry (WP4 maps to 404)."""


class ArchivedProfile(Exception):
    """The profile is registered but archived (WP4 maps to 410)."""


def config_factory(pid: str) -> Callable[[], Config]:
    """Return a callable that resolves the derived config for profile ``pid`` fresh on
    every call (§4.1).

    On EACH call it: ``load_config()`` (which already derives the install-wide active
    ``llm_configs`` entry onto ``cfg.llm``) → re-reads the profile's ``ProfileMeta``
    from the registry (never a captured snapshot, so rename/accent edits are picked up) →
    ``with_profile(meta)``. The LLM is common across profiles now, so there is no
    per-profile settings overlay — a config change reloads every runtime.
    """

    def resolve() -> Config:
        cfg = load_config()
        meta = profiles.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        return cfg.with_profile(meta)

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


class ProfileRuntime:
    """One profile's live runtime: gateway + task service + logger."""

    def __init__(
        self,
        meta: ProfileMeta,
        *,
        memory: bool = True,
        persist: bool = True,
        notifier: Callable | None = None,
    ) -> None:
        self.meta = meta
        self._memory = memory
        self._persist = persist
        self._config: Config | None = None
        self.gateway: Gateway | None = None
        self.tasks = None
        # ``(platform, chat_id, text)`` push for task-run outcomes. Channels are
        # install-level, so this reaches out to whoever owns them (the manager).
        self._notifier = notifier
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
        meta = profiles.get_profile(self.pid)
        if meta is not None:
            self.meta = meta

    def on_close(self, callback: Callable) -> None:
        """Register a callback fired when this runtime is archived/closed (WP4 wires
        its WS handlers here to close sockets with code 4001)."""
        self._close_callbacks.append(callback)

    async def notify_channel(self, platform: str, chat_id: str, text: str) -> None:
        """Push a message to a platform chat — the task service delivers run outcomes
        through here. The Channel is install-level, so the push is handed to the
        notifier this runtime was built with."""
        if self._notifier is None:
            raise RuntimeError(f"channel {platform!r} is not reachable from this runtime")
        await self._notifier(platform, chat_id, text)

    async def start(self) -> None:
        """Build the derived config, construct + start gateway and task service the same
        way the base wiring does. Channels are not started here — they are install-level
        and owned by the ProfileManager (ADR 0019).
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

    def __init__(self, *, memory: bool = True, persist: bool = True) -> None:
        self._memory = memory
        self._persist = persist
        self._runtimes: dict[str, ProfileRuntime] = {}
        # platform → the live install-level adapter (ADR 0019). One per platform for
        # the whole install; never owned by a runtime.
        self.channels: dict[str, channels.Channel] = {}
        # platform → last start-failure message (bad/missing token, network). Install-
        # level, surfaced in GET /api/channels; cleared on a successful start or stop.
        self.channel_errors: dict[str, str] = {}
        # The one router every adapter is handed. It resolves the profile per inbound
        # message through the directory methods below, so changing a Channel's default
        # profile — or a Peer's own selection — needs no restart.
        self.router = channels.ChannelRouter(self)

    # ---- the router's ProfileDirectory (read fresh on every message) ----

    def available_profiles(self) -> tuple[channels.AvailableProfile, ...]:
        """Every profile a platform conversation could be pointed at right now —
        the running runtimes, named as their user sees them."""
        return tuple(
            channels.AvailableProfile(r.meta.id, r.meta.name) for r in self._runtimes.values()
        )

    def default_profile(self, platform: str) -> str | None:
        """The Channel's default profile, or None when it has none or the profile it
        names is not running."""
        pid = profiles.channel_defaults().get(platform)
        return pid if pid in self._runtimes else None

    def gateway_for_profile(self, pid: str) -> Gateway | None:
        """The running gateway for a profile id, or None when it is not running."""
        runtime = self._runtimes.get(pid)
        return runtime.gateway if runtime is not None else None

    async def start(self) -> None:
        """Boot every UNARCHIVED registered profile, then start every Channel whose
        tokens are configured.

        Zero profiles is a legal no-op (fresh install, §3.5). Logging is set up once
        against the root config here so per-profile loggers write to the shared file.
        """
        setup_logging(load_config())
        for meta in profiles.list_profiles(include_archived=False):
            await self._boot(meta)
        for platform in profiles.CHANNEL_PLATFORMS:
            await self.start_channel(platform)

    async def _boot(self, meta: ProfileMeta) -> ProfileRuntime:
        runtime = ProfileRuntime(
            meta,
            memory=self._memory,
            persist=self._persist,
            notifier=self.notify_channel,
        )
        await runtime.start()
        self._runtimes[meta.id] = runtime
        return runtime

    async def notify_channel(self, platform: str, chat_id: str, text: str) -> None:
        """Push a message into a platform chat through the install's live Channel —
        task-run outcomes are delivered this way."""
        channel = self.channels.get(platform)
        if channel is None:
            raise RuntimeError(f"channel {platform!r} is not running")
        await channel.notify(chat_id, text)

    async def start_channel(self, platform: str) -> tuple[bool, str | None]:
        """Start ``platform`` at install level if its tokens are present, handing it the
        shared router. Guarded: a bad token / network failure logs + records
        ``channel_errors[platform]`` and returns (False, reason) instead of crashing —
        a Channel that cannot connect must never take the server down with it. Success
        clears any prior error. Already running is a no-op.

        Returns ``(active, reason)``: active True iff the Channel is now live."""
        log = profile_logger("default")
        if platform in self.channels:
            return True, None
        envs = _CHANNEL_TOKENS[platform]
        if not all(os.environ.get(e) for e in envs):
            msg = f"no token configured for {platform}"
            self.channel_errors[platform] = msg
            return False, msg
        # A channel's start() talks to the platform (Telegram get_me, Discord/Slack
        # connect) and RAISES on a bad token / network failure — as does get_channel
        # when a token is missing. Never let that propagate: it would 500 the endpoint
        # and crash boot. Record the reason, stay inactive.
        try:
            channel = channels.get_channel(platform)
            await channel.start(self.router)
        except Exception as exc:
            # Platform libraries embed the raw token in some error messages
            # (e.g. Telegram's "The token <value> was rejected"); scrub it —
            # this string is logged AND returned via GET /api/channels.
            msg = _scrub_tokens(f"could not start '{platform}': {exc}", envs)
            log.error(msg)
            self.channel_errors[platform] = msg
            return False, msg
        self.channels[platform] = channel
        self.channel_errors.pop(platform, None)
        log.info("channel '%s' started", platform)
        return True, None

    async def stop_channel(self, platform: str) -> bool:
        """Stop ``platform`` if it is live. Returns True if a live Channel was stopped."""
        channel = self.channels.pop(platform, None)
        if channel is None:
            return False
        try:
            await channel.stop()
        except Exception as exc:
            log_suppressed("channel stop", exc)
        self.channel_errors.pop(platform, None)
        profile_logger("default").info("channel '%s' stopped", platform)
        return True

    async def restart_channel(self, platform: str) -> tuple[bool, str | None]:
        """Re-apply the live state of ``platform`` after its tokens changed (e.g. a
        token was saved/cleared via the secrets store): stop it, then start it again if
        all its tokens are now present (guarded). Returns ``(active, reason)`` like
        ``start_channel``. Raises ``ValueError`` for an unknown platform."""
        if platform not in profiles.CHANNEL_PLATFORMS:
            raise ValueError(
                f"unknown channel platform: {platform} "
                f"(choose from {', '.join(profiles.CHANNEL_PLATFORMS)})"
            )
        await self.stop_channel(platform)
        return await self.start_channel(platform)

    async def close(self) -> None:
        """Stop every Channel, then close all running runtimes."""
        for platform in list(self.channels):
            await self.stop_channel(platform)
        for runtime in list(self._runtimes.values()):
            await runtime.close()
        self._runtimes.clear()
        self.channel_errors.clear()

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

    def runtimes_by_id(self) -> dict[str, ProfileRuntime]:
        """The running runtimes keyed by pid (a copy; safe to read without raising for
        an absent/archived pid, unlike ``get``)."""
        return dict(self._runtimes)

    @property
    def active_default(self) -> str | None:
        return profiles.load_registry().get("active_default")

    async def create(self, name: str, accent: str) -> ProfileRuntime:
        """Create a profile (registry + dir) and boot its runtime live (§3.5).
        ``accent`` is a ``#rrggbb`` hex (ADR 0002)."""
        meta = profiles.create_profile(name, accent)
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

        profiles.archive_profile(pid)

    async def restore(self, pid: str) -> ProfileRuntime:
        """Un-archive a profile and boot its runtime live (§4.9, ADR 0003).

        Symmetric with ``create``: clear the archived flag then ``_boot``. All-or-
        nothing — if boot fails the flag is rolled back to archived so the profile is
        never left in the unarchived-but-not-running state ``get`` treats as a server
        bug. Unknown → UnknownProfile; a live (non-archived) profile → ValueError.
        """
        meta = profiles.get_profile(pid)
        if meta is None:
            raise UnknownProfile(pid)
        if not meta.archived:
            raise ValueError(f"profile is not archived: {pid}")

        restored = profiles.restore_profile(pid)
        try:
            return await self._boot(restored)
        except Exception:
            # Roll back the flag so the invariant "unarchived ⟺ running" holds.
            profiles.archive_profile(pid)
            self._runtimes.pop(pid, None)
            raise

    async def purge(self, pid: str) -> None:
        """Permanently delete an ARCHIVED profile: erase its folder and drop its
        registry entry (§4.9, ADR 0003). The only state-destroying operation.

        Archive-first: refuses a live profile (ValueError → 409) so delete never has to
        tear down a running runtime, reassign the active default, or hit the last-profile
        guardrail — an archived profile is already none of those. Unknown → UnknownProfile.
        """
        meta = profiles.get_profile(pid)
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
        shutil.rmtree(profiles.profile_dir(pid), ignore_errors=True)
        profiles.delete_profile(pid)
