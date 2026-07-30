"""§4.8 acceptance gates as tests — pin the global-path allowlist so no future
code can quietly reintroduce a per-profile leak via a global default.

Three static guards (no runtime, pure source scan):

1. ``test_no_global_path_defaults`` — every ``data_dir()`` / ``Path.home()`` hit
   under ``src/assistant`` must live in the pinned allowlist (§4.8). Profile-owned
   modules (settings, memory, usage[non-pricing], permissions, tasks store, hitl
   inquiry, observability, onboarding) must have ZERO hits.
2. ``test_settings_importers_pass_paths`` — no module importing ``assistant.settings``
   may call ``Settings()`` with empty parens (the path must always be explicit).
3. ``test_with_profile_overrides_every_path_field`` — iterate Config's ``Path``
   model fields; ``with_profile()`` must move each away from its legacy root-level
   value, except the intentional exception ``root_dir``.
"""

import re
from pathlib import Path

from assistant import profiles
from assistant.config import Config

SRC = Path(__file__).resolve().parent.parent / "src" / "assistant"

# Files permitted to call data_dir() / Path.home() — the complete set of
# INTENTIONAL globals (§4.8 acceptance gate). Paths are relative to SRC.
_ALLOWLIST = {
    "config.py",  # root resolution itself
    "secrets.py",  # global secrets.json
    "llm_configs.py",  # install-wide named LLM configs (llm_configs.json), like secrets
    "codex_auth.py",  # global ChatGPT-subscription tokens (account-level, like google_auth)
    "profiles.py",  # the registry + default-workspace seed
    "peers.py",  # install-level Peer registry (peers.json) — spans profiles by design (ADR 0019)
    "pairing.py",  # install-level Channel allowlist (pairing.json) — per Channel, not per profile
    "connections.py",  # install-level Connection registry (connections.json) — never per profile
    "usage.py",  # pricing read ONLY (asserted below)
    "integrations/google_auth.py",  # global OAuth files (routed via data_dir())
    "gateway/app.py",  # fs-browser Path.home() starting points ONLY
}

# Modules that MUST have zero hits after their §4.8 rows landed.
_MUST_BE_CLEAN = {
    "observability.py",
    "permissions.py",
    "tasks/store.py",
    "hitl/inquiry.py",
    "memory.py",
    "settings.py",
    "onboarding.py",
}

_HIT = re.compile(r"data_dir\(\)|Path\.home\(\)")


def _iter_hits():
    """Yield (relpath, lineno, text) for every data_dir()/Path.home() hit in src."""
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        for i, line in enumerate(path.read_text().splitlines(), start=1):
            if _HIT.search(line):
                yield rel, i, line.strip()


def test_no_global_path_defaults():
    """Every data_dir()/Path.home() hit must be in the pinned allowlist, and the
    profile-owned modules must be entirely clean."""
    hits = list(_iter_hits())
    offenders = [(rel, ln, txt) for rel, ln, txt in hits if rel not in _ALLOWLIST]
    assert not offenders, "global path default(s) outside the §4.8 allowlist:\n" + "\n".join(
        f"  {rel}:{ln}: {txt}" for rel, ln, txt in offenders
    )

    # the modules whose §4.8 rows removed their global defaults must have NO hits
    dirty = sorted({rel for rel, _, _ in hits if rel in _MUST_BE_CLEAN})
    assert not dirty, f"modules expected to be free of global path defaults still hit one: {dirty}"


def test_usage_data_dir_is_pricing_only():
    """usage.py is allowlisted only for its pricing read — assert the single hit's
    line context is the pricing.json read, not a usage/profile-owned path."""
    usage_hits = [(ln, txt) for rel, ln, txt in _iter_hits() if rel == "usage.py"]
    assert usage_hits, "expected usage.py to read pricing.json via data_dir()"
    for _ln, txt in usage_hits:
        assert "pricing" in txt, (
            f"usage.py data_dir() hit is not the pricing read (profile-owned leak?): {txt}"
        )


def test_google_auth_uses_data_dir_not_path_home():
    """google_auth.py routes its (intentionally global) OAuth files through data_dir()
    — not a second hardcoded Path.home() construction (§4.8)."""
    rel = "integrations/google_auth.py"
    hits = [txt for r, _ln, txt in _iter_hits() if r == rel]
    assert hits, "expected google_auth.py to locate its OAuth files via data_dir()"
    assert all("Path.home()" not in txt for txt in hits), (
        "google_auth.py must locate OAuth files via data_dir(), not Path.home()"
    )


def test_settings_importers_pass_paths():
    """No module importing assistant.settings may call ``Settings()`` with empty
    parens — a cheap guard that the per-profile settings path is always explicit."""
    empty_ctor = re.compile(r"\bSettings\(\s*\)")
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text()
        if "assistant.settings" not in text and "from assistant import settings" not in text:
            # only modules that actually pull in the settings module are relevant
            if "import settings" not in text and "Settings(" not in text:
                continue
        for i, line in enumerate(text.splitlines(), start=1):
            if empty_ctor.search(line):
                offenders.append((path.relative_to(SRC).as_posix(), i, line.strip()))
    assert not offenders, (
        "Settings() called without an explicit path (per-profile path must be passed):\n"
        + "\n".join(f"  {rel}:{ln}: {txt}" for rel, ln, txt in offenders)
    )


def test_with_profile_overrides_every_path_field():
    """Iterate Config's Path model fields; with_profile() must change each one away
    from its legacy root-level value, EXCEPT the intentional exception ``root_dir``
    (which by design still carries the root)."""

    meta = profiles.create_profile("Work", "#109e91")
    base = Config()
    derived = base.with_profile(meta)

    path_fields = [name for name, field in Config.model_fields.items() if field.annotation is Path]
    # sanity: the known path fields are present
    assert set(path_fields) >= {"root_dir", "data_dir", "skills_dir", "workspace_dir"}

    for name in path_fields:
        base_val = getattr(base, name)
        derived_val = getattr(derived, name)
        if name == "root_dir":
            assert derived_val == base_val, "root_dir is the intentional exception (stays the root)"
        else:
            assert derived_val != base_val, (
                f"with_profile() left path field {name!r} at its legacy root-level "
                f"location ({derived_val}) — installed state would leak across profiles"
            )
