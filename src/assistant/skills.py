"""Skill layers + the single resolution seam (CONTEXT.md "Skills", ADR 0016).

Skills used to be all-or-nothing on disk: present meant available everywhere,
absent meant gone. A **Disabled** state — a skill kept on disk but dropped from the
agent's ``<available_skills>`` catalog — is recorded by skill *name* in the shared
``StateStore``, over the install-wide ``root_dir/skills.json`` document. Delete
(on-disk removal) stays a filesystem concern; the store never touches skill files.

Resolution is DEFAULT-ON: a skill is available unless a record turns it off. See
``assistant.state_store`` for the machinery and for why not to "fix" that.
"""

from collections.abc import Callable
from pathlib import Path

from ag2.exceptions import SkillNotFoundError

from assistant.state_store import ORIGIN_BUNDLED, ORIGIN_GLOBAL, ORIGIN_PROFILE, StateStore


def skill_origin(location: str | None, bundled_root: Path, profile_root: Path | None = None) -> str:
    """Classify a discovered skill's layer from its on-disk location: under the
    active profile skills dir → ``profile``; under the bundled first-party dir →
    ``bundled``; otherwise → ``global``."""
    if not location:
        return ORIGIN_GLOBAL
    try:
        loc = Path(location).resolve()
        bundled = bundled_root.resolve()
        profile = profile_root.resolve() if profile_root is not None else None
    except (OSError, ValueError, RuntimeError):
        return ORIGIN_GLOBAL
    if profile is not None and (loc == profile or profile in loc.parents):
        return ORIGIN_PROFILE
    return ORIGIN_BUNDLED if loc == bundled or bundled in loc.parents else ORIGIN_GLOBAL


SKILLS_DOCUMENT = "skills.json"


class SkillStateStore(StateStore):
    """Skill state over the install-wide ``skills.json`` document at ``root_dir``:
    which skills are Disabled, and which are turned off for one profile alone."""

    def __init__(self, root_dir: Path | None) -> None:
        # ``None`` is an explicit ephemeral, non-persisting store (tests/fallback).
        super().__init__(Path(root_dir) / SKILLS_DOCUMENT if root_dir is not None else None)


class FilteredSkillRuntime:
    """A ``SkillRuntime`` view that hides skills resolved unavailable by a predicate.

    Wraps a concrete runtime and drops the unavailable skills from discovery (so
    they never reach the ``<available_skills>`` catalog or the activation tools'
    name enum) AND refuses to read/execute them (defence-in-depth: even if a name
    slips through, a Disabled skill cannot be loaded or run). Everything else —
    storage, lock dir, install/remove, invalidate — delegates to the inner
    runtime unchanged, so the registry install tools keep operating on the full
    set.
    """

    def __init__(self, inner, is_available: Callable[[object], bool]) -> None:
        self._inner = inner
        self._is_available = is_available

    @property
    def skills(self):
        return [skill for skill in self._inner.skills if self._is_available(skill)]

    def read(self, name: str) -> str:
        self._guard(name)
        return self._inner.read(name)

    async def read_resource(self, name: str, resource: str, context) -> str:
        self._guard(name)
        return await self._inner.read_resource(name, resource, context)

    async def execute(self, name: str, script: str, context, args=None) -> str:
        self._guard(name)
        return await self._inner.execute(name, script, context, args)

    def _guard(self, name: str) -> None:
        skill = next((item for item in self._inner.skills if item.name == name), None)
        if skill is not None and not self._is_available(skill):
            # Same signal the toolkit's multi-runtime chain uses for "not mine",
            # so a disabled skill reads exactly like an absent one.
            raise SkillNotFoundError(f"Skill {name!r} is not available")

    def __getattr__(self, item):
        # Delegate the rest of the SkillRuntime protocol (cleanup, lock_dir,
        # invalidate, ensure_storage, install, remove, …) to the inner runtime.
        return getattr(self._inner, item)
