"""Phase-7 routes answer bodies their response models accept.

The 13 routes here — the settings panel, the MCP list, the voice picker and the
transient HITL queue — run empty first and populated second, the two states that
stress a model in opposite directions. Empty is where a field wrongly declared
required turns a green route into a 500: a fresh profile has no MCP server, no
focuses, no model override, no key and no open question, and a transient HITL
prompt carries neither a detail nor a fixed set of options.

The state this phase has to watch hardest is the one where the model would ADD a
key. ``GET /settings`` carries two shapes that are unions in all but name — a
provider key entry is ``{set, hint}`` or ``{set, base_url}``, never both — so the
route runs ``exclude_unset`` and the assertions here are on the key SET, not just
on the values. Without it the wire grows a ``hint: null`` that the zod twin
declares ``.optional()`` and rejects, which is a front-end crash, not a warning.
"""

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from assistant.hitl import Question
from assistant.profiles import ProfileRegistry
from tests.support.apps import api, make_manager, make_paths, make_profile_app


async def _live_ok(_provider, _key):
    """A live_probe that reports the key as usable without leaving the process."""


@pytest.fixture
def client(paths):
    """One started profile whose live-config Test never reaches a provider."""
    app, pid = make_profile_app(paths, persist=True, live_probe=_live_ok)
    with TestClient(app) as c:
        yield c, pid


def _save_live(client, **kw):
    """A named live (voice) config, for the config-scoped branch of the picker."""
    r = client.post("/api/live-configs", json={"name": "Voice", "provider": "openai", **kw})
    assert r.status_code == 200, r.text
    return r.json()["config"]


# ---- GET /settings ----


def test_settings_on_a_fresh_profile(client):
    """Everything the panel needs is present on an install that has configured
    nothing: no key, no config, no override, no MCP server, no focus."""
    c, pid = client
    r = c.get(api(pid, "/settings"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "keys",
        "voice_available",
        "assistant",
        "llm_override",
        "llm_active",
        "live_override",
        "live_active",
        "codex",
        "voice_provider",
        "mcp_servers",
        "focuses",
        "reply_timeout_s",
        "fs",
    }
    assert body["mcp_servers"] == []
    assert body["focuses"] == []
    # No config saved and none pinned: both switchers inherit, and there is
    # nothing to inherit either.
    assert body["llm_override"] is None
    assert body["llm_active"] is None
    assert body["live_override"] is None
    assert body["live_active"] is None
    assert body["codex"] == {
        "signed_in": False,
        "source": None,
        "account_id": None,
        "expires_at": None,
    }
    assert set(body["assistant"]) == {"provider", "model"}
    assert set(body["fs"]) == {"home", "cwd", "workspace"}
    assert set(body["voice_available"]) == {"gemini", "openai"}


def test_a_key_entry_carries_only_the_half_its_provider_has(client):
    """secrets.status() answers {set, hint} per LLM provider and {set, base_url}
    for Ollama. The model declares all three, so without exclude_unset each entry
    would grow the other's field as null — which zod, declaring both `.optional()`,
    rejects outright."""
    c, pid = client
    keys = c.get(api(pid, "/settings")).json()["keys"]
    assert set(keys["openai"]) == {"set", "hint"}
    assert set(keys["ollama"]) == {"set", "base_url"}
    assert keys["openai"]["set"] is False


def test_a_saved_key_and_a_pinned_config_show_up_in_settings(client):
    """The populated pass: a key is set, a live config exists and is Active."""
    c, pid = client
    assert c.post("/api/secrets/key", json={"provider": "openai", "value": "sk-abc"}).json() == {
        "ok": True
    }
    cfg = _save_live(c, api_key="sk-abc")
    assert c.post(f"/api/live-configs/{cfg['id']}/use").json() == {"ok": True}

    body = c.get(api(pid, "/settings")).json()
    assert body["keys"]["openai"]["set"] is True
    assert body["keys"]["openai"]["hint"]  # last-4 hint, never the raw key
    assert body["voice_available"]["openai"] is True
    # Install-wide Active with no per-profile override: active resolves, override
    # stays null. The pair is the ADR 0015 contract the header switcher reads.
    assert body["live_override"] is None
    assert body["live_active"] == cfg["id"]


# ---- MCP servers ----


def test_an_mcp_row_is_the_public_projection(client):
    """A server's `env` never rides a settings fetch — its key NAMES do, under
    env_keys, and the same row shape comes back from the write and from the list."""
    c, pid = client
    r = c.post(
        api(pid, "/settings/mcp"),
        json={"name": "local", "command": "__missing__", "args": "--flag", "env": "TOKEN=secret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    row_keys = {
        "name",
        "enabled",
        "command",
        "args",
        "cwd",
        "allowed_tools",
        "blocked_tools",
        "env_keys",
    }
    assert set(body["server"]) == row_keys
    assert body["server"]["env_keys"] == ["TOKEN"]
    assert set(body["mcp_servers"][0]) == row_keys
    assert set(c.get(api(pid, "/settings")).json()["mcp_servers"][0]) == row_keys


def test_deleting_an_mcp_server_answers_the_list_alone(client):
    """The row it names is gone, so the snapshot is all there is to send."""
    c, pid = client
    c.post(api(pid, "/settings/mcp"), json={"name": "local", "command": "__missing__"})
    r = c.delete(api(pid, "/settings/mcp/local"))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "mcp_servers": []}
    assert c.delete(api(pid, "/settings/mcp/local")).status_code == 404


def test_an_unreachable_mcp_server_is_a_200_with_ok_false(client):
    """A probe that fails is a fact about the server, not a transport failure —
    the second union member, and the branch the panel renders inline."""
    c, pid = client
    c.post(api(pid, "/settings/mcp"), json={"name": "local", "command": "__missing__"})
    r = c.post(api(pid, "/settings/mcp/local/health"))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert set(body) == {"ok", "error"}
    assert c.post(api(pid, "/settings/mcp/ghost/health")).status_code == 404


# ---- the single-field writes ----


def test_focuses_echo_what_the_store_normalised(client):
    """The route answers the STORED list, not the posted one — which is why it
    echoes at all rather than answering a bare {ok}."""
    c, pid = client
    r = c.post(api(pid, "/settings/focuses"), json={"focuses": ["Coding", "coding", "research"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "focuses": ["coding", "research"]}
    assert c.post(api(pid, "/settings/focuses"), json={"focuses": []}).json() == {
        "ok": True,
        "focuses": [],
    }


def test_the_model_overrides_answer_null_when_cleared(client):
    """Set then clear, for both switchers: an empty config_id is a clear, and the
    body says so with null rather than an empty string."""
    c, pid = client
    cfg = _save_live(c, api_key="sk-abc")
    assert c.post(api(pid, "/settings/live-override"), json={"config_id": cfg["id"]}).json() == {
        "ok": True,
        "live_override": cfg["id"],
    }
    assert c.post(api(pid, "/settings/live-override"), json={"config_id": ""}).json() == {
        "ok": True,
        "live_override": None,
    }
    # The Text switcher over an empty config store: clearing is still a 200, and
    # an unknown id is a 404 whose body is built by hand, not by the model.
    assert c.post(api(pid, "/settings/llm-override"), json={"config_id": ""}).json() == {
        "ok": True,
        "llm_override": None,
    }
    assert c.post(
        api(pid, "/settings/llm-override"), json={"config_id": "c_ghost"}
    ).status_code == (404)


def test_reply_timeout_echoes_the_stored_float(client):
    c, pid = client
    r = c.post(api(pid, "/settings/reply-timeout"), json={"reply_timeout_s": 480})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "reply_timeout_s": 480.0}
    assert c.get(api(pid, "/settings")).json()["reply_timeout_s"] == 480.0


def test_the_voice_provider_needs_its_key_first(client):
    """Without a key the route answers 409 with an {ok, error} body of its own
    making; with one it is the bare acknowledgement."""
    c, pid = client
    refused = c.post(api(pid, "/settings/voice_provider"), json={"provider": "gemini"})
    assert refused.status_code == 409
    c.post("/api/secrets/key", json={"provider": "gemini", "value": "gm-abc"})
    r = c.post(api(pid, "/settings/voice_provider"), json={"provider": "gemini"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ---- voice picker ----


def test_the_voice_catalogue_carries_the_profile_selection(client):
    c, pid = client
    r = c.get(api(pid, "/voice/voices"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"voices", "current", "provider", "input_rate"}
    assert set(body["voices"][0]) == {"name", "style"}
    # No voice was ever picked, so the profile scope falls back to the provider's
    # default — a string, never null.
    assert isinstance(body["current"], str)
    assert isinstance(body["input_rate"], int)


def test_the_config_scope_reads_and_writes_that_config(client):
    """``?config_id=`` swaps the whole scope: the catalogue is that config's
    provider's, `current` is the voice the CONFIG persisted, and a select writes
    there rather than onto the profile's legacy setting. `current` stays
    required-nullable because it is a plain lookup into a stored row — a save
    fills it in, but the route does not put a default in front of it."""
    c, pid = client
    cfg = _save_live(c, api_key="sk-abc")
    body = c.get(api(pid, f"/voice/voices?config_id={cfg['id']}")).json()
    assert body["provider"] == "openai"
    assert body["current"] == cfg["voice"]

    voice = next(v["name"] for v in body["voices"] if v["name"] != cfg["voice"])
    r = c.post(api(pid, "/voice/select"), json={"voice": voice, "config_id": cfg["id"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "voice": voice}
    assert c.get(api(pid, f"/voice/voices?config_id={cfg['id']}")).json()["current"] == voice


def test_selecting_an_unknown_voice_is_a_400_with_no_body(client):
    c, pid = client
    assert c.post(api(pid, "/voice/select"), json={"voice": "Nope"}).status_code == 400


def test_the_preview_is_bytes_and_declares_no_json_body(client):
    """response_model=None is the hatch for a non-JSON route; the only thing to
    check on the contract side is that a bad voice still refuses before synthesis."""
    c, pid = client
    assert c.post(api(pid, "/voice/preview"), json={"voice": "Nope"}).status_code == 400


# ---- transient HITL queue ----


def test_the_hitl_queue_is_empty_on_a_fresh_profile(client):
    c, pid = client
    r = c.get(api(pid, "/hitl/pending"))
    assert r.status_code == 200
    assert r.json() == {"pending": []}


async def test_a_question_without_a_detail_survives_the_model(paths):
    """The row that broke the old zod schema: Question defaults `detail` and
    `options` to None, so both must be nullable on the wire. A permission prompt
    is exactly this shape, and the strip renders nothing for a body it cannot
    parse — the failure was silent."""
    registry = ProfileRegistry(paths)
    meta = registry.create_profile("Test", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = make_manager(paths)
    from assistant.gateway.app import create_app

    app = create_app(manager)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as http:
        async with app.router.lifespan_context(app):
            runtime = manager.get(meta.id)
            bare, _ = runtime.hitl.register(Question(text="Run this?", kind="permission"))
            full, _ = runtime.hitl.register(
                Question(text="Which one?", detail="pick a branch", options=["a", "b"])
            )

            rows = {
                q["id"]: q
                for q in (await http.get(api(meta.id, "/hitl/pending"))).json()["pending"]
            }
    assert set(rows[bare]) == {"id", "text", "detail", "options", "kind", "path"}
    assert rows[bare]["detail"] is None
    assert rows[bare]["options"] is None
    assert rows[bare]["kind"] == "permission"
    assert rows[bare]["path"] == f"/hitl/{bare}"
    assert rows[full]["detail"] == "pick a branch"
    assert rows[full]["options"] == ["a", "b"]


def test_the_health_rollup_is_shaped_for_the_status_dot(client):
    """Phase 1 landed this route; phase 7 moves the rest of its module in beside
    it, so the roll-up runs once more here — the rows that carry no extra detail
    must still not grow `servers`/`items` as nulls."""
    c, pid = client
    body = c.get(api(pid, "/health")).json()
    assert body["overall"] in ("ok", "warn", "down")
    checks = {row["id"]: row for row in body["checks"]}
    assert set(checks["agent"]) == {"id", "label", "state", "detail"}
    assert checks["mcp"]["servers"] == []
    assert checks["channels"]["items"] == []


def test_an_unknown_profile_is_a_404_on_every_settings_route(paths):
    """The moved routes still resolve their runtime through get_runtime, so the
    404/410 contract is unchanged by the move."""
    app, _pid = make_profile_app(make_paths(paths.root.parent), persist=True)
    with TestClient(app) as c:
        assert c.get(api("ghost", "/settings")).status_code == 404
        assert c.get(api("ghost", "/voice/voices")).status_code == 404
        assert c.get(api("ghost", "/hitl/pending")).status_code == 404
