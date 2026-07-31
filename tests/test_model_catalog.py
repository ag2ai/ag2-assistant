"""The ACP model-catalog probe feeding the Settings picker.

``parse`` is a pure function over an adapter's ``session/new`` result. Everything
else is exercised against REAL adapter stubs on a real search path: an executable
that answers the two probe requests, one that answers with an empty catalog, and
one that dies. Each ModelCatalog owns its cache, so no fixture has to reset state.
"""

import asyncio
import json

import pytest

from assistant.coding.detect import BridgeEndpoint
from assistant.coding.model_catalog import CatalogModel, ModelCatalog, as_view, parse

_INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
_CATALOG = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "models": {
                "currentModelId": "m",
                "availableModels": [{"modelId": "m", "name": "M"}],
            }
        },
    }
)
_EMPTY_CATALOG = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {}})


def _dying_adapter(bin_dir, name="codex-acp"):
    """An adapter that consumes the request and then dies without answering. It reads
    first on purpose: a stub exiting before we write would break the pipe on the WRITE
    side, which is a different (racy) failure than "the adapter closed the pipe"."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text("#!/bin/sh\nhead -n 1 >/dev/null\nexit 1\n")
    script.chmod(0o755)
    return bin_dir


def _hanging_adapter(bin_dir, name="codex-acp"):
    """An adapter that never answers, so only the probe timeout can end the wait."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    return bin_dir


def _reply_groups(lines):
    """Group the stub's output lines per request: everything up to and including the
    next line carrying an ``id`` answers one request (a notification in between is
    unsolicited, so it rides with the reply that follows it)."""
    groups, pending = [], []
    for line in lines:
        pending.append(line)
        try:
            answers_a_request = json.loads(line).get("id") is not None
        except ValueError:
            answers_a_request = False
        if answers_a_request:
            groups.append(pending)
            pending = []
    if pending:
        groups.append(pending)
    return groups


def _adapter(bin_dir, name="codex-acp", *, lines=(_INIT, _CATALOG), marker=None, noise=()):
    """A real executable adapter stub: optional launcher noise, then one reply per
    request — it READS each request before answering and stays alive afterwards, the
    way a real adapter does. A stub that answers blindly and exits closes the pipe
    under the prober's next write, which surfaces as a load-dependent
    ConnectionResetError (reproduced 12/250 times under CPU load).
    Each spawn appends to ``marker``, so tests can count adapter launches."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    count = f"echo x >> {marker}\n" if marker is not None else ""
    banner = "".join(f"printf '%s\\n' {json.dumps(line)}\n" for line in noise)
    body = "".join(
        "read _request || exit 0\n"
        + "".join(f"printf '%s\\n' {json.dumps(line)}\n" for line in group)
        for group in _reply_groups(lines)
    )
    script.write_text(f"#!/bin/sh\n{count}{banner}{body}exec cat >/dev/null\n")
    script.chmod(0o755)
    return bin_dir


# ---- parse (pure) --------------------------------------------------------------


def test_parse_codex_models_shape():
    models, current = parse(
        {
            "models": {
                "currentModelId": "gpt-5.6-sol[low]",
                "availableModels": [
                    {
                        "modelId": "gpt-5.6-sol[low]",
                        "name": "GPT-5.6-Sol (low)",
                        "description": "d",
                    },
                    {"modelId": "gpt-5.5[high]", "name": "GPT-5.5 (high)"},
                    {"name": "ghost, no id"},
                ],
            }
        }
    )
    assert [m.id for m in models] == ["gpt-5.6-sol[low]", "gpt-5.5[high]"]
    assert models[0].description == "d" and models[1].description == ""
    assert current == "gpt-5.6-sol[low]"


def test_parse_claude_config_options_shape():
    models, current = parse(
        {
            "configOptions": [
                {"id": "mode", "category": "mode", "options": [{"value": "ask", "name": "Ask"}]},
                {
                    "id": "model",
                    "category": "model",
                    "currentValue": "opus[1m]",
                    "options": [
                        {"value": "default", "name": "Default (recommended)", "description": "…"},
                        {"value": "opus[1m]", "name": "Opus", "description": "1M context"},
                        {"value": "sonnet", "name": "Sonnet"},
                        {"name": "ghost, no value"},
                    ],
                },
            ]
        }
    )
    # The adapter's "default" row is dropped: for us that case IS an empty model
    # (no model env derived), and sending the literal "default" would just fail.
    assert [m.id for m in models] == ["opus[1m]", "sonnet"]
    assert models[0].description == "1M context"
    assert current == "opus[1m]"


def test_parse_normalises_default_current_to_empty():
    # currentValue "default" means "whatever the CLI is set to" → our empty model.
    models, current = parse(
        {
            "configOptions": [
                {
                    "category": "model",
                    "currentValue": "default",
                    "options": [{"value": "default", "name": "Default"}, {"value": "sonnet"}],
                }
            ]
        }
    )
    assert [m.id for m in models] == ["sonnet"]
    assert current == ""


def test_parse_tolerates_odd_shapes():
    assert parse({}) == ([], "")
    assert parse({"models": "nope"}) == ([], "")
    assert parse({"models": {"availableModels": None}}) == ([], "")
    assert parse({"configOptions": [{"category": "mode"}]}) == ([], "")


# ---- probing a real adapter ----------------------------------------------------


async def test_probe_reads_the_catalog_of_a_real_adapter(tmp_path):
    catalog = ModelCatalog(search_path=[_adapter(tmp_path / "bin")])
    models, current = await catalog.list_models("codex")
    assert [m.id for m in models] == ["m"] and current == "m"


async def test_probe_skips_non_json_and_notification_lines(tmp_path):
    bin_dir = _adapter(
        tmp_path / "bin",
        noise=("npm notice new version available", "not json at all"),
        lines=(
            _INIT,
            json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {}}),
            _CATALOG,
        ),
    )
    models, current = await ModelCatalog(search_path=[bin_dir]).list_models("codex")
    # A stray banner line must not sink the whole catalog.
    assert [m.id for m in models] == ["m"] and current == "m"


async def test_probe_raises_when_the_adapter_closes_the_pipe(tmp_path):
    bin_dir = _dying_adapter(tmp_path / "bin")
    with pytest.raises(RuntimeError, match="closed the pipe"):
        await ModelCatalog(search_path=[bin_dir]).list_models("codex")


async def test_a_missing_adapter_is_an_empty_catalog(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert await ModelCatalog(search_path=[bin_dir]).list_models("codex") == ([], "")


# ---- caching -------------------------------------------------------------------


async def test_list_models_caches_per_agent(tmp_path):
    bin_dir = tmp_path / "bin"
    codex_spawns, claude_spawns = tmp_path / "codex", tmp_path / "claude"
    _adapter(bin_dir, "codex-acp", marker=codex_spawns)
    _adapter(bin_dir, "claude-agent-acp", marker=claude_spawns)
    catalog = ModelCatalog(search_path=[bin_dir])

    await catalog.list_models("codex")
    await catalog.list_models("codex")  # TTL cache absorbs the repeat
    await catalog.list_models("claude")  # separate cache slot
    assert codex_spawns.read_text().count("x") == 1
    assert claude_spawns.read_text().count("x") == 1

    await catalog.list_models("codex", refresh=True)
    assert codex_spawns.read_text().count("x") == 2


async def test_an_expired_entry_is_reprobed(tmp_path):
    marker = tmp_path / "spawns"
    bin_dir = _adapter(tmp_path / "bin", marker=marker)
    now = [1000.0]
    catalog = ModelCatalog(search_path=[bin_dir], cache_ttl=300.0, clock=lambda: now[0])

    await catalog.list_models("codex")
    now[0] += 299.0
    await catalog.list_models("codex")
    assert marker.read_text().count("x") == 1
    now[0] += 2.0  # past the TTL
    await catalog.list_models("codex")
    assert marker.read_text().count("x") == 2


async def test_list_models_never_caches_empty(tmp_path):
    marker = tmp_path / "spawns"
    bin_dir = _adapter(tmp_path / "bin", lines=(_INIT, _EMPTY_CATALOG), marker=marker)
    catalog = ModelCatalog(search_path=[bin_dir])
    await catalog.list_models("codex")
    await catalog.list_models("codex")
    # The user may be mid-`npm i -g`: an empty catalog must be re-probed.
    assert marker.read_text().count("x") == 2


async def test_two_catalogs_do_not_share_a_cache(tmp_path):
    marker = tmp_path / "spawns"
    bin_dir = _adapter(tmp_path / "bin", marker=marker)
    await ModelCatalog(search_path=[bin_dir]).list_models("codex")
    await ModelCatalog(search_path=[bin_dir]).list_models("codex")
    assert marker.read_text().count("x") == 2


async def test_list_models_shares_one_probe_between_concurrent_callers(tmp_path):
    marker = tmp_path / "spawns"
    bin_dir = _adapter(tmp_path / "bin", marker=marker)
    catalog = ModelCatalog(search_path=[bin_dir])
    a, b = await asyncio.gather(catalog.list_models("codex"), catalog.list_models("codex"))
    # Two form opens (or a double render) must not spawn two adapter subprocesses.
    assert marker.read_text().count("x") == 1
    assert a == b


async def test_list_models_clears_inflight_after_failure(tmp_path):
    catalog = ModelCatalog(search_path=[_dying_adapter(tmp_path / "bin")])
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await catalog.list_models("codex")
    # A failed probe must not leave a poisoned in-flight entry behind.
    assert catalog._inflight == {}


async def test_a_hanging_adapter_is_bounded_by_the_probe_timeout(tmp_path):
    catalog = ModelCatalog(search_path=[_hanging_adapter(tmp_path / "bin")], probe_timeout=0.05)
    with pytest.raises(TimeoutError):
        await catalog.list_models("codex")


# ---- reason + view -------------------------------------------------------------


def test_unavailable_reason(tmp_path):
    bin_dir = _adapter(tmp_path / "bin")
    assert ModelCatalog(search_path=[bin_dir]).unavailable_reason("codex") == ""
    assert ModelCatalog(search_path=[bin_dir]).unavailable_reason("claude") == "adapter_missing"
    # A bridge means the adapter runs on the HOST — no local spawn, no catalog,
    # even though the entry itself is usable in that mode.
    bridged = ModelCatalog(search_path=[bin_dir], bridge=BridgeEndpoint("h", 1, ""))
    assert bridged.unavailable_reason("codex") == "bridge"


def test_as_view_shape():
    view = as_view([CatalogModel("a[low]", "A (low)", "d")], "a[low]")
    assert view == {
        "models": [{"id": "a[low]", "name": "A (low)", "description": "d"}],
        "current": "a[low]",
        "reason": "",
    }
    assert as_view([], "", "adapter_missing") == {
        "models": [],
        "current": "",
        "reason": "adapter_missing",
    }
