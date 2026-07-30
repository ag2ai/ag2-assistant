"""Acceptance gates as tests: the environment is read at the boundary, nowhere else.

After the DI refactor the invariant is no longer "who may call ``data_dir()``" (that
function is gone) but:

  ``os.environ`` / ``os.getenv`` / ``Path.home()`` are read ONLY in the entry points
  — ``cli.py``, ``paths.py::Paths.from_env`` and ``config.py::load_config`` — and
  everything below takes a resolved ``Paths`` / ``Config`` / explicit env mapping.

The scan is an AST walk (not a regex), so prose in comments and docstrings can't
trip it and no real read can hide from it. Modules that still read the environment
are pinned in ``_DEFERRED`` with the plan task that removes them; the list is
asserted to be free of stale rows, so it can only shrink.
"""

import ast
from pathlib import Path

from assistant.config import Config
from assistant.integrations.google_auth import GoogleAuth
from assistant.profiles import ProfileRegistry
from assistant.usage import UsageLedger

SRC = Path(__file__).resolve().parent.parent / "src" / "assistant"

# The entry points: resolving the ambient environment is their whole job.
_BOUNDARY = {
    "cli.py",  # the CLI entry point wires everything from os.environ
    "paths.py",  # Paths.from_env — the one place the layout comes from
    "config.py",  # load_config — composes resolve_config with the real environment
}

# Still reading the environment below the boundary, each with the task that ends it.
# A row that no longer has a hit is a failure: this list may only shrink.
_DEFERRED = {
    "onboarding.py": "Task 26 — writes AG2ASSISTANT_LOCATION instead of returning it",
    "agent.py": "Task 26 — the /.dockerenv + TZ probe becomes an argument",
    "codex_auth.py": "Task 18 leftover — _const reads env at import time",
    "coding/bridge_server.py": "Task 25 — the spawned adapter's env whitelist",
    "gateway/app.py": "Task 24 — Path.home() starting points for the fs browser",
}

# load_config() re-reads the process environment, so calling it below the boundary is
# the same leak by another name. These are the remaining ``config or load_config()``
# fallbacks; each disappears when its caller is always handed a Config.
_LOAD_CONFIG_DEFERRED = {
    "memory.py": "Task 26 — the knowledge store takes a Config",
    "agent.py": "Task 26 — create_agent/turn_prompt always receive a Config",
    "gateway/core.py": "Task 27 — Gateway is always built with a Config",
    "gateway/tasks_service.py": "Task 26 — TaskService is always built with a Config",
    "gateway/profile_manager.py": "Task 27 — the manager always resolves from paths",
}


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text())


def _is_attr(node, obj: str, attr: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == obj
    )


def _env_hits(tree) -> list[int]:
    """Line numbers of every real os.environ / os.getenv / Path.home() access."""
    lines = []
    for node in ast.walk(tree):
        if _is_attr(node, "os", "environ") or _is_attr(node, "os", "getenv"):
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and _is_attr(node.func, "Path", "home"):
            lines.append(node.lineno)
    return lines


def _load_config_calls(tree) -> list[int]:
    """Line numbers of every load_config() call."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_config"
    ]


def _dirty(finder) -> dict[str, list[int]]:
    return {rel: hits for rel, tree in _modules() if (hits := finder(tree))}


def test_the_environment_is_read_only_at_the_boundary():
    """No module outside the entry points and the pinned deferred list may touch the
    process environment or $HOME."""
    dirty = _dirty(_env_hits)
    offenders = {
        rel: hits for rel, hits in dirty.items() if rel not in _BOUNDARY and rel not in _DEFERRED
    }
    assert not offenders, (
        "environment read below the boundary — take a resolved Paths/Config or an "
        "explicit env mapping instead:\n"
        + "\n".join(f"  {rel}:{hits}" for rel, hits in sorted(offenders.items()))
    )


def test_the_deferred_environment_list_has_no_stale_rows():
    """Every pinned exception must still be real, so the list can only shrink."""
    dirty = _dirty(_env_hits)
    stale = sorted(rel for rel in _DEFERRED if rel not in dirty)
    assert not stale, f"these modules are clean now — drop them from _DEFERRED: {stale}"


def test_load_config_is_called_only_at_the_boundary():
    """``load_config()`` re-reads os.environ, so below the boundary it is the same
    leak: those callers must be handed a Config instead."""
    dirty = _dirty(_load_config_calls)
    offenders = {
        rel: hits
        for rel, hits in dirty.items()
        if rel not in _BOUNDARY and rel not in _LOAD_CONFIG_DEFERRED
    }
    assert not offenders, "load_config() called below the boundary:\n" + "\n".join(
        f"  {rel}:{hits}" for rel, hits in sorted(offenders.items())
    )


def test_the_deferred_load_config_list_has_no_stale_rows():
    dirty = _dirty(_load_config_calls)
    stale = sorted(rel for rel in _LOAD_CONFIG_DEFERRED if rel not in dirty)
    assert not stale, f"these modules no longer call load_config(): {stale}"


def test_no_module_resolves_the_layout_for_itself():
    """``Paths.from_env`` is the boundary's tool: a module below it that calls the
    classmethod would re-derive the layout instead of taking the resolved one."""
    offenders = {}
    for rel, tree in _modules():
        if rel in _BOUNDARY:
            continue
        hits = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_attr(node.func, "Paths", "from_env")
        ]
        if hits:
            offenders[rel] = hits
    assert not offenders, f"Paths.from_env called below the boundary: {offenders}"


def test_the_stores_take_their_paths_explicitly(paths, tmp_path):
    """Spot-check the install-wide stores that used to derive global locations
    themselves: each one's files now hang off the Paths it was handed."""
    google = GoogleAuth(paths)
    assert google.credentials_path == paths.google_credentials
    assert google.token_path == paths.google_token
    assert google.account_path == paths.google_account

    # The usage ledger keeps the profile's ledger and the install-wide pricing file
    # apart, and neither may be defaulted.
    ledger = UsageLedger(tmp_path / "usage.json", pricing_path=paths.root / "pricing.json")
    assert ledger._pricing_path == paths.root / "pricing.json"
    try:
        UsageLedger(tmp_path / "usage.json")
    except TypeError:
        pass
    else:
        raise AssertionError("UsageLedger must not default its pricing path")


def test_with_profile_overrides_every_path_field(paths):
    """Iterate Config's Path model fields; with_profile() must change each one away
    from its legacy root-level value, EXCEPT the intentional exception ``root_dir``
    (which by design still carries the root)."""
    meta = ProfileRegistry(paths).create_profile("Work", "#109e91")
    base = Config.for_paths(paths)
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


def test_settings_importers_pass_paths():
    """No module may call ``Settings()`` with empty parens — a cheap guard that the
    per-profile settings path is always explicit."""
    offenders = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Settings"
                and not node.args
                and not node.keywords
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"Settings() called without an explicit path: {offenders}"
