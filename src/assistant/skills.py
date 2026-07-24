"""Skill state store + the single resolution seam (CONTEXT.md "Skills", ADR 0016).

Skills used to be all-or-nothing on disk: present meant available everywhere,
absent meant gone. This store adds a **Disabled** state — a skill kept on disk
but dropped from the agent's ``<available_skills>`` catalog — recorded
install-wide by skill *name*. Delete (on-disk removal) stays a filesystem
concern; this store never touches the skill files.

The one JSON document (``root_dir/skills.json``) uses the same concurrency
machinery as the Folder/permission stores: mtime self-refresh (a long-lived
gateway sees CLI/API writes), a cross-process exclusive lock around every
read-modify-write, and atomic replace on save. It is **state**, not
configuration — never threaded through ``Config``/``settings.json``.

⚠️  DEFAULT-ON, the inverse of a Folders Grant (ADR 0006). A skill is available
unless a record turns it off — absence of a record means "on", where a Folder is
unreachable unless a Grant opts it in. Do **not** "fix" this into a default-deny
Grant: skills are capabilities you *add*, and flipping the default would
dark-start every profile's skills on upgrade (ADR 0016, rejected alternative).

The store holds two things: the install-wide **Disabled** set (by skill name) and
per-profile **Suppression** records ``(profile_id, skill_name)`` — a shared skill
turned off for one profile only (ADR 0016). Resolution folds both together in
``is_available``: a skill is available to a profile unless it is Disabled
install-wide OR Suppressed for that profile. Absence of a Suppression record means
inherit "on" — the same default-on intent as the install-wide set, and the inverse
of a Folders Grant.
"""

import contextlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from ag2.exceptions import SkillNotFoundError

from assistant.permissions import _lock_exclusive, _unlock

# A skill's layer (ADR 0016 glossary). Bundled ships with the app (read-only);
# Global is user-installed at the Root and shared by every profile. Named here so
# the string lives in one place, the way Folders name their modes (folders.READ).
ORIGIN_BUNDLED = "bundled"
ORIGIN_GLOBAL = "global"
# A skill installed inside one profile (its own skills_dir): visible to that
# profile only. Its off-state is a Suppression record like a shared skill's — the
# distinction is purely which surface (and label) the user toggles it from.
ORIGIN_PROFILE = "profile"

# The kind of a per-profile off-record. Both turn a skill off for one profile, but
# they answer to different Deletes, so the record carries which it is (ADR 0016):
#   • SHARED — a Suppression of an inherited Bundled/Global skill; a Global Delete's
#     cascade purge clears these.
#   • OWN — a Disable of the profile's OWN skill; cleared only when that copy is
#     deleted, never by a same-named Global purge.
# A name-only record can't tell the two apart, so a Global purge would wrongly wipe a
# same-named Profile skill's own off-state (and vice versa). The kind prevents that.
SUPPRESS_SHARED = "shared"
DISABLE_OWN = "own"


def skill_origin(location: str | None, bundled_root: Path) -> str:
    """Classify a discovered skill's layer from its on-disk location: under the
    bundled first-party dir → ``bundled``, otherwise → ``global``. Kept beside the
    store so origin is decided in the skills domain, not at the HTTP layer."""
    if not location:
        return ORIGIN_GLOBAL
    try:
        loc = Path(location).resolve()
        root = bundled_root.resolve()
    except (OSError, ValueError, RuntimeError):
        return ORIGIN_GLOBAL
    return ORIGIN_BUNDLED if loc == root or root in loc.parents else ORIGIN_GLOBAL


class SkillStateStore:
    """Persistent record of install-wide skill state (currently: which skills are
    Disabled). Keyed by skill name; default-on (ADR 0016)."""

    def __init__(self, path: Path | None) -> None:
        # ``path`` is REQUIRED for persistence. Pass ``None`` for an explicit
        # ephemeral, non-persisting store (un-wired fallback / tests).
        self._path = Path(path) if path is not None else None
        self._disabled: list[str] = []
        # Per-profile off-records: (profile_id, skill_name, kind) triples, kept
        # sorted. ``kind`` is SUPPRESS_SHARED (an inherited skill turned off here) or
        # DISABLE_OWN (this profile's own skill disabled) — see the constants above.
        self._suppressed: list[tuple[str, str, str]] = []
        self._stat: tuple[int, int] | None = None
        self._load()

    # --- persistence (same shape as FolderStore: refresh / lock / atomic) ---

    def _load(self) -> None:
        self._disabled = []
        self._suppressed = []
        self._stat = None
        if self._path is None:
            return
        try:
            st = self._path.stat()
        except OSError:
            return
        self._stat = (st.st_mtime_ns, st.st_size)
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return
        if not isinstance(data, dict):
            return
        raw = data.get("disabled")
        self._disabled = (
            sorted({str(n) for n in raw if isinstance(n, str)}) if isinstance(raw, list) else []
        )
        raw_sup = data.get("suppressed")
        triples: set[tuple[str, str, str]] = set()
        if isinstance(raw_sup, list):
            for r in raw_sup:
                if not isinstance(r, dict):
                    continue
                prof = str(r.get("profile", "")).strip()
                name = str(r.get("name", "")).strip()
                # Records written before the kind tag meant "suppression of a shared
                # skill" — the original semantics — so default to SHARED.
                kind = str(r.get("kind", "")).strip() or SUPPRESS_SHARED
                if kind not in (SUPPRESS_SHARED, DISABLE_OWN):
                    kind = SUPPRESS_SHARED
                if prof and name:
                    triples.add((prof, name, kind))
        self._suppressed = sorted(triples)

    def _refresh(self) -> None:
        if self._path is None:
            return
        try:
            st = self._path.stat()
            current: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            current = None
        if current != self._stat:
            self._load()

    @contextlib.contextmanager
    def _mutate(self):
        if self._path is None:
            yield
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.parent / (self._path.name + ".lock")
        with open(lock_path, "w") as lock:
            _lock_exclusive(lock)
            try:
                self._refresh()
                yield
            finally:
                _unlock(lock)

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "disabled": sorted(self._disabled),
                "suppressed": [
                    {"profile": p, "name": n, "kind": k} for p, n, k in sorted(self._suppressed)
                ],
            },
            indent=2,
        )
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".skills.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        try:
            st = self._path.stat()
            self._stat = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._stat = None

    # --- reads ---

    def disabled_names(self) -> set[str]:
        """The install-wide Disabled skill names (a copy)."""
        self._refresh()
        return set(self._disabled)

    def is_disabled(self, name: str) -> bool:
        """Whether ``name`` is Disabled install-wide."""
        self._refresh()
        return (name or "").strip() in self._disabled

    def suppressed_names(self, profile: str) -> set[str]:
        """The skill names turned off for ``profile`` (a copy), either kind."""
        self._refresh()
        profile = (profile or "").strip()
        return {n for p, n, _k in self._suppressed if p == profile}

    def is_suppressed(self, name: str, profile: str) -> bool:
        """Whether ``name`` is turned off for ``profile`` (off for this profile only).
        Resolution ignores the record's kind — any off-record means unavailable."""
        self._refresh()
        profile = (profile or "").strip()
        name = (name or "").strip()
        return any(p == profile and n == name for p, n, _k in self._suppressed)

    def is_available(self, name: str, profile: str = "") -> bool:
        """The single resolution seam: is skill ``name`` available to ``profile``?

        DEFAULT-ON — available unless turned off (ADR 0016; see the module note).
        A skill is unavailable if it is Disabled install-wide OR Suppressed for this
        profile. Absence of either record means "on". Do NOT invert this to
        default-deny. ``profile`` empty = the install-wide answer (no Suppression
        applies), which is what the Application → Skills view asks.
        """
        if self.is_disabled(name):
            return False
        if profile and self.is_suppressed(name, profile):
            return False
        return True

    # --- mutations ---

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or Disable ``name`` install-wide. Idempotent; no-op if the skill
        is unknown to disk (the store keys by name, so it simply records intent)."""
        name = (name or "").strip()
        if not name:
            raise ValueError("skill name is required")
        with self._mutate():
            present = name in self._disabled
            if enabled and present:
                self._disabled = [n for n in self._disabled if n != name]
                self._save()
            elif not enabled and not present:
                self._disabled.append(name)
                self._save()

    def set_suppressed(
        self, name: str, profile: str, suppressed: bool, kind: str = SUPPRESS_SHARED
    ) -> None:
        """Turn ``name`` off (or back on) for one ``profile`` only. ``kind`` records
        WHICH off-state this is — SUPPRESS_SHARED for an inherited skill, DISABLE_OWN
        for the profile's own skill — so a later Global purge / profile-copy delete
        clears only the record it means. Idempotent; keys by (profile, name, kind), so
        it never touches another profile's resolution nor the other kind's record for
        the same (profile, name)."""
        name = (name or "").strip()
        profile = (profile or "").strip()
        if not name:
            raise ValueError("skill name is required")
        if not profile:
            raise ValueError("profile is required")
        if kind not in (SUPPRESS_SHARED, DISABLE_OWN):
            raise ValueError(f"unknown off-record kind: {kind!r}")
        with self._mutate():
            rec = (profile, name, kind)
            present = rec in self._suppressed
            if suppressed and not present:
                self._suppressed = sorted([*self._suppressed, rec])
                self._save()
            elif not suppressed and present:
                self._suppressed = [r for r in self._suppressed if r != rec]
                self._save()

    def purge(self, name: str) -> None:
        """Drop the shared-skill records for ``name`` — its install-wide Disable and
        every profile's Suppression of it — so a later same-named re-install resolves
        default-on everywhere (ADR 0016 ticket 03). This is the cascade a **Global**
        Delete runs after removing the files; it mirrors ``FolderStore.delete_folder``
        dropping a folder's grants. Idempotent: a no-op (and no write) when the store
        holds no such record for ``name``.

        DISABLE_OWN records are left standing: a same-named Profile-owned skill's off
        state belongs to that profile's own copy, not to the Global skill being
        deleted — only that copy's own Delete clears it."""
        name = (name or "").strip()
        if not name:
            raise ValueError("skill name is required")
        with self._mutate():
            has_disabled = name in self._disabled
            keep = [r for r in self._suppressed if not (r[1] == name and r[2] == SUPPRESS_SHARED)]
            if not has_disabled and len(keep) == len(self._suppressed):
                return  # nothing shared-scoped recorded for this skill — no write
            self._disabled = [n for n in self._disabled if n != name]
            self._suppressed = keep
            self._save()


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

    def __init__(self, inner, is_available: Callable[[str], bool]) -> None:
        self._inner = inner
        self._is_available = is_available

    @property
    def skills(self):
        return [s for s in self._inner.skills if self._is_available(s.name)]

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
        if not self._is_available(name):
            # Same signal the toolkit's multi-runtime chain uses for "not mine",
            # so a disabled skill reads exactly like an absent one.
            raise SkillNotFoundError(f"Skill {name!r} is not available")

    def __getattr__(self, item):
        # Delegate the rest of the SkillRuntime protocol (cleanup, lock_dir,
        # invalidate, ensure_storage, install, remove, …) to the inner runtime.
        return getattr(self._inner, item)
