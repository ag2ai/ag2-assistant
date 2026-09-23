"""Default-on Enable/Disable state over one JSON document (ADR 0016).

A **StateStore** records which named things are turned off: an install-wide
**Disabled** set plus typed per-profile off-records, keyed by name. Each store owns
one document — Skill state lives in ``skills.json``, a Card catalog's in its own —
so a Skill and a Card sharing a name cannot share a switch.

The document carries the same concurrency machinery as the Folder/permission
stores: mtime self-refresh (a long-lived gateway sees CLI/API writes), a
cross-process exclusive lock around every read-modify-write, and atomic replace on
save. It is **state**, not configuration — never threaded through
``Config``/``settings.json``.

⚠️  DEFAULT-ON, the inverse of a Folders Grant (ADR 0006). A thing is available
unless a record turns it off — absence of a record means "on", where a Folder is
unreachable unless a Grant opts it in. Do **not** "fix" this into a default-deny
Grant: these are capabilities you *add*, and flipping the default would dark-start
everything a profile already has on upgrade (ADR 0016, rejected alternative).
"""

import contextlib
import json
import os
import tempfile
from pathlib import Path

from assistant.permissions import _lock_exclusive, _unlock

# A layer (ADR 0016 glossary): Bundled ships with the app read-only, Global is
# installed at the Root for every profile, Profile belongs to one profile alone.
ORIGIN_BUNDLED = "bundled"
ORIGIN_GLOBAL = "global"
ORIGIN_PROFILE = "profile"

# The kind of a per-profile off-record. Both turn a thing off for one profile, but
# they answer to different Deletes, so the record carries which it is (ADR 0016):
#   • SHARED — a Suppression of an inherited Bundled/Global thing; a Global Delete's
#     cascade purge clears these.
#   • OWN — a Disable of the profile's OWN copy; cleared only when that copy is
#     deleted, never by a same-named Global purge.
# A name-only record can't tell the two apart, so a Global purge would wrongly wipe a
# same-named Profile-owned thing's own off-state (and vice versa). The kind prevents that.
SUPPRESS_SHARED = "shared"
DISABLE_OWN = "own"


class StateStore:
    """Persistent record of install-wide state (which names are Disabled) plus
    per-profile off-records, over one document. Keyed by name; default-on."""

    def __init__(self, path: Path | None) -> None:
        # ``path`` is REQUIRED for persistence. Pass ``None`` for an explicit
        # ephemeral, non-persisting store (un-wired fallback / tests).
        self._path = Path(path) if path is not None else None
        self._disabled: list[str] = []
        # Per-profile off-records: (profile_id, name, kind) triples, kept sorted.
        # ``kind`` is SUPPRESS_SHARED (an inherited thing turned off here) or
        # DISABLE_OWN (this profile's own copy disabled) — see the constants above.
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
                # thing" — the original semantics — so default to SHARED.
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
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=f".{self._path.stem}.", suffix=".tmp"
        )
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
        """The install-wide Disabled names (a copy)."""
        self._refresh()
        return set(self._disabled)

    def is_disabled(self, name: str) -> bool:
        """Whether ``name`` is Disabled install-wide."""
        self._refresh()
        return (name or "").strip() in self._disabled

    def suppressed_names(self, profile: str) -> set[str]:
        """The names turned off for ``profile`` (a copy), either kind."""
        self._refresh()
        profile = (profile or "").strip()
        return {n for p, n, _k in self._suppressed if p == profile}

    def is_suppressed(self, name: str, profile: str, kind: str | None = None) -> bool:
        """Whether ``name`` is turned off for ``profile``.

        ``kind`` selects a shared-layer Suppression or Profile-layer Disable. With no
        kind, either record counts for callers that do not know the resolved layer.
        """
        self._refresh()
        profile = (profile or "").strip()
        name = (name or "").strip()
        return any(
            p == profile and n == name and (kind is None or record_kind == kind)
            for p, n, record_kind in self._suppressed
        )

    def is_available(self, name: str, profile: str = "", origin: str = ORIGIN_GLOBAL) -> bool:
        """The single resolution seam: is ``name`` available to ``profile``?

        DEFAULT-ON — available unless turned off (ADR 0016; see the module note).
        Shared layers resolve against install-wide Disable plus this profile's shared
        Suppression. A Profile-layer thing resolves only against its own per-profile
        Disable, so same-named shared state never leaks through the shadow.
        """
        if origin == ORIGIN_PROFILE:
            return not (profile and self.is_suppressed(name, profile, kind=DISABLE_OWN))
        if origin not in (ORIGIN_GLOBAL, ORIGIN_BUNDLED):
            raise ValueError(f"unknown origin: {origin!r}")
        if self.is_disabled(name):
            return False
        if profile and self.is_suppressed(name, profile, kind=SUPPRESS_SHARED):
            return False
        return True

    # --- mutations ---

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or Disable ``name`` install-wide. Idempotent; no-op if the name
        is unknown to disk (the store keys by name, so it simply records intent)."""
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")
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
        WHICH off-state this is — SUPPRESS_SHARED for an inherited thing, DISABLE_OWN
        for the profile's own copy — so a later Global purge / profile-copy delete
        clears only the record it means. Idempotent; keys by (profile, name, kind), so
        it never touches another profile's resolution nor the other kind's record for
        the same (profile, name)."""
        name = (name or "").strip()
        profile = (profile or "").strip()
        if not name:
            raise ValueError("name is required")
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
        """Drop the shared-layer records for ``name`` — its install-wide Disable and
        every profile's Suppression of it — so a later same-named re-install resolves
        default-on everywhere. This is the cascade a **Global**
        Delete runs after removing the files; it mirrors ``FolderStore.delete_folder``
        dropping a folder's grants. Idempotent: a no-op (and no write) when the store
        holds no such record for ``name``.

        DISABLE_OWN records are left standing: a same-named Profile-owned thing's off
        state belongs to that profile's own copy, not to the Global one being
        deleted — only that copy's own Delete clears it."""
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")
        with self._mutate():
            has_disabled = name in self._disabled
            keep = [r for r in self._suppressed if not (r[1] == name and r[2] == SUPPRESS_SHARED)]
            if not has_disabled and len(keep) == len(self._suppressed):
                return  # nothing shared-scoped recorded for this name — no write
            self._disabled = [n for n in self._disabled if n != name]
            self._suppressed = keep
            self._save()
