"""Phase-3 routes answer bodies their response models accept.

The 28 routes here — llm-configs, live-configs, secrets, google, codex — run on
the state that stresses their model. Empty first: a model that made a
sometimes-absent field required would turn a green route into a 500. Then
populated, because the model is the contract, so a key it forgot to declare
disappears from the wire silently instead of failing loudly. Where a body is
shaped by a branch (a subscription config's `signed_in`, a Secret's `used_by`,
Google's ok/error union), each branch gets its own assertion.

Every probe is a fake: a real `llm_probe` builds an agent and calls a provider,
which no unit test may do.
"""

import ag2
import ag2.testing
import pytest
from fastapi.testclient import TestClient

from assistant.secrets import SecretStore
from tests.support.apps import make_manager, make_paths, make_profile_app, write_codex_session

# A saved secret is what makes a config's `secret`/`key_source` fields interesting;
# every LLM body below is checked against this exact field list.
LLM_CONFIG_KEYS = {
    "id",
    "name",
    "type",
    "model",
    "base_url",
    "host",
    "options",
    "secret_id",
    "secret",
    "secret_missing",
    "key_source",
    "images",
    "deps",
    "shared_key",
    "active",
}

LIVE_CONFIG_KEYS = {
    "id",
    "name",
    "provider",
    "model",
    "voice",
    "secret_id",
    "secret",
    "secret_missing",
    "key_source",
    "shared_key",
    "active",
}


def _probe_ok(_config):
    """An llm_probe whose derived config answers PONG in-process, so a save's
    dry-construct and a Test's round-trip both run for real without a provider."""

    class _Cfg(ag2.testing.TestConfig):
        def create(self):
            return ag2.testing.TestClient("PONG")

    return _Cfg()


async def _live_ok(_provider, _key):
    """A live_probe that reports the key as usable without leaving the process."""


@pytest.fixture
def client(paths):
    """One profile, both probes faked, so save and Test never reach a provider."""
    app, _pid = make_profile_app(paths, llm_probe=_probe_ok, live_probe=_live_ok)
    with TestClient(app) as c:
        yield c


def _save_llm(client, **kw):
    body = {"name": "Main", "type": "openai", "model": "gpt-4o", **kw}
    r = client.post("/api/llm-configs", json=body)
    assert r.status_code == 200, r.text
    return r.json()["config"]


def _save_live(client, **kw):
    body = {"name": "Voice", "provider": "openai", **kw}
    r = client.post("/api/live-configs", json=body)
    assert r.status_code == 200, r.text
    return r.json()["config"]


# ---- secrets ----


def test_the_secret_list_is_empty_and_well_shaped_on_a_fresh_install(tmp_path):
    app, _pid = make_profile_app(make_paths(tmp_path))
    with TestClient(app) as c:
        r = c.get("/api/secrets")
    assert r.status_code == 200
    assert r.json() == {"secrets": []}


def test_a_saved_secret_carries_its_view_but_claims_nothing_about_references(client):
    """The save routes answer with the store's bare view, which says nothing about
    which configs point at the Secret — so `used_by` must be absent, not `[]`."""
    r = client.post("/api/secrets", json={"name": "Key", "value": "sk-1", "provider": "openai"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert set(body["secret"]) == {"id", "name", "provider", "default", "hint"}
    assert body["secret"]["hint"].endswith("sk-1"[-4:])


def test_the_list_route_is_the_one_that_reports_used_by(client):
    """`used_by` names the configs referencing each Secret; it exists only here."""
    sid = client.post("/api/secrets", json={"name": "Key", "value": "sk-2"}).json()["secret"]["id"]
    _save_llm(client, secret_id=sid)
    rows = client.get("/api/secrets").json()["secrets"]
    assert [row["used_by"] for row in rows] == [["Main"]]


def test_renaming_a_secret_answers_the_new_view(client):
    sid = client.post("/api/secrets", json={"name": "Old", "value": "sk-3"}).json()["secret"]["id"]
    r = client.post(f"/api/secrets/{sid}", json={"name": "New"})
    assert r.status_code == 200
    assert r.json()["secret"]["name"] == "New"
    assert "used_by" not in r.json()["secret"]


def test_deleting_a_secret_answers_the_bare_acknowledgement(client):
    sid = client.post("/api/secrets", json={"name": "Go", "value": "sk-4"}).json()["secret"]["id"]
    assert client.delete(f"/api/secrets/{sid}").json() == {"ok": True}


def test_saving_a_provider_key_answers_the_bare_acknowledgement(client):
    assert client.post("/api/secrets/key", json={"provider": "openai", "value": "sk-5"}).json() == {
        "ok": True
    }


# ---- llm-configs ----


def test_the_llm_config_list_is_empty_and_well_shaped_on_a_fresh_install(tmp_path):
    """`env_override` is null with neither variable set, and `provider_deps` covers
    every known type rather than only the configured ones."""
    app, _pid = make_profile_app(make_paths(tmp_path))
    with TestClient(app) as c:
        body = c.get("/api/llm-configs").json()
    assert body["configs"] == []
    assert body["active"] is None
    assert body["env_override"] is None
    assert body["provider_deps"]
    for deps in body["provider_deps"].values():
        assert set(deps) == {"ok", "extra", "install"}


def test_a_saved_llm_config_declares_every_field_the_row_renders(client):
    saved = _save_llm(client)
    assert set(saved) == LLM_CONFIG_KEYS
    assert saved["secret"] is None
    assert set(saved["shared_key"]) == {"env", "set", "hint"}
    assert saved["key_source"] in {"secret", "shared", "not_needed", "none", "subscription"}


def test_a_config_pointing_at_a_secret_embeds_that_secret_s_trimmed_view(client):
    sid = client.post("/api/secrets", json={"name": "K", "value": "sk-6"}).json()["secret"]["id"]
    saved = _save_llm(client, secret_id=sid)
    assert set(saved["secret"]) == {"id", "name", "hint"}
    assert saved["secret_missing"] is False
    assert saved["key_source"] == "secret"


def test_only_a_subscription_config_carries_signed_in(client):
    """`signed_in` is added for one type alone, so it must be absent — never null —
    on every other row, which is what the zod twin's `.optional()` demands."""
    plain = _save_llm(client)
    assert "signed_in" not in plain
    sub = _save_llm(client, name="ChatGPT", type="openai_subscription", model="gpt-5")
    assert sub["signed_in"] is False


def test_the_env_override_banner_reports_only_the_variables_that_are_set(tmp_path):
    """Each key appears only when its variable does — absent, not null."""
    app, _pid = make_profile_app(make_paths(tmp_path), env={"AG2ASSISTANT_MODEL": "gpt-4o-mini"})
    with TestClient(app) as c:
        override = c.get("/api/llm-configs").json()["env_override"]
    assert override == {"model": "gpt-4o-mini"}


def test_listing_after_a_save_reports_the_config_as_active(client):
    _save_llm(client, activate=True)
    body = client.get("/api/llm-configs").json()
    assert len(body["configs"]) == 1
    assert body["configs"][0]["active"] is True
    assert body["active"] == body["configs"][0]["id"]


def test_updating_a_config_answers_the_same_envelope_as_creating_one(client):
    cid = _save_llm(client)["id"]
    r = client.post(
        f"/api/llm-configs/{cid}", json={"name": "Renamed", "type": "openai", "model": "gpt-4o"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["config"]["name"] == "Renamed"
    assert set(body["config"]) == LLM_CONFIG_KEYS


def test_use_and_delete_answer_the_bare_acknowledgement(client):
    cid = _save_llm(client)["id"]
    assert client.post(f"/api/llm-configs/{cid}/use").json() == {"ok": True}
    assert client.delete(f"/api/llm-configs/{cid}").json() == {"ok": True}


def test_a_saved_config_test_reports_the_reply_and_the_latency(paths):
    """The PONG round-trip's success body. The agent is the manager's fake, so the
    'provider call' is in-process."""
    app, _pid = make_profile_app(paths, llm_probe=_probe_ok)
    with TestClient(app) as c:
        cid = _save_llm(c)["id"]
        r = c.post(f"/api/llm-configs/{cid}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["reply"] == "PONG"
    assert isinstance(body["latency_ms"], int)


def test_a_draft_test_answers_the_same_shape_without_saving(paths):
    app, _pid = make_profile_app(paths, llm_probe=_probe_ok)
    with TestClient(app) as c:
        r = c.post(
            "/api/llm-configs/test",
            json={"name": "draft", "type": "openai", "model": "gpt-4o", "api_key": "sk-typed"},
        )
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"ok", "reply", "latency_ms"}
        # "Without saving" is the point: the store is still empty.
        assert c.get("/api/llm-configs").json()["configs"] == []


def test_the_provider_catalog_answers_the_models_current_reason_envelope(paths):
    async def probe(_target):
        return ["gpt-4o", "gpt-4o-mini"]

    app, _pid = make_profile_app(paths, llm_catalog_probe=probe)
    with TestClient(app) as c:
        r = c.get("/api/llm-configs/models?type=openai")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == ""
    assert body["reason"] == ""
    assert [m["id"] for m in body["models"]] == ["gpt-4o", "gpt-4o-mini"]
    for row in body["models"]:
        assert set(row) == {"id", "name", "description"}


def test_a_catalog_that_cannot_be_probed_says_why(paths):
    """`reason` is the whole reason this envelope exists — an empty list alone
    would leave the form unable to explain itself."""
    app, _pid = make_profile_app(paths)
    with TestClient(app) as c:
        body = c.get("/api/llm-configs/models?type=openai_subscription").json()
    assert body == {"models": [], "current": "", "reason": "not_probeable"}


# ---- live-configs ----


def test_the_live_config_list_is_empty_but_still_names_the_providers(tmp_path):
    """`providers` seeds the add-form, so it is populated even with no configs."""
    app, _pid = make_profile_app(make_paths(tmp_path))
    with TestClient(app) as c:
        body = c.get("/api/live-configs").json()
    assert body["configs"] == []
    assert body["active"] is None
    assert body["providers"]
    for provider in body["providers"]:
        assert set(provider) == {"name", "default_model", "default_voice"}


def test_a_saved_live_config_declares_every_field_the_row_renders(client):
    saved = _save_live(client)
    assert set(saved) == LIVE_CONFIG_KEYS
    assert saved["secret"] is None
    assert saved["key_source"] in {"secret", "shared", "none"}


def test_updating_a_live_config_answers_the_same_envelope(client):
    cid = _save_live(client)["id"]
    r = client.post(f"/api/live-configs/{cid}", json={"name": "Renamed", "provider": "openai"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert set(r.json()["config"]) == LIVE_CONFIG_KEYS


def test_live_use_and_delete_answer_the_bare_acknowledgement(client):
    cid = _save_live(client)["id"]
    assert client.post(f"/api/live-configs/{cid}/use").json() == {"ok": True}
    assert client.delete(f"/api/live-configs/{cid}").json() == {"ok": True}


def test_both_live_tests_answer_the_probe_envelope(client):
    """The saved and the draft probe share one body shape."""
    cid = _save_live(client)["id"]
    saved = client.post(f"/api/live-configs/{cid}/test")
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"ok": True, "reply": "OK", "latency_ms": saved.json()["latency_ms"]}

    draft = client.post(
        "/api/live-configs/test", json={"name": "draft", "provider": "openai", "api_key": "sk-x"}
    )
    assert draft.status_code == 200, draft.text
    assert set(draft.json()) == {"ok", "reply", "latency_ms"}


# ---- google ----


def test_google_status_on_an_install_with_no_oauth_client(tmp_path):
    app, _pid = make_profile_app(make_paths(tmp_path))
    with TestClient(app) as c:
        body = c.get("/api/google/status").json()
    assert set(body) == {"configured", "signed_in", "email", "libs_available", "install_hint"}
    assert body["configured"] is False
    assert body["signed_in"] is False
    assert body["email"] is None


def test_a_bad_oauth_client_json_is_a_200_with_the_reason(client):
    """Both branches of this route are 200 — the union is the contract, and the
    error branch has to keep its message rather than being filtered away."""
    r = client.post("/api/google/credentials", json={"content": "not json"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["error"]


def test_asking_for_a_login_url_without_a_client_answers_the_error_branch(client):
    r = client.post("/api/google/login_url")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "No OAuth client configured."}


def test_google_logout_answers_the_bare_acknowledgement(client):
    body = client.post("/api/google/logout").json()
    assert set(body) == {"ok"}
    assert isinstance(body["ok"], bool)


# ---- codex ----


def test_codex_status_signed_out_declares_every_key_as_null(tmp_path):
    """Signed out, three of the four fields are None — none of them may vanish."""
    app, _pid = make_profile_app(make_paths(tmp_path))
    with TestClient(app) as c:
        body = c.get("/api/codex/status").json()
    assert body == {"signed_in": False, "source": None, "account_id": None, "expires_at": None}


def test_codex_status_signed_in_reports_the_source_and_the_expiry(paths):
    write_codex_session(paths, account_id="acc-1")
    app, _pid = make_profile_app(paths)
    with TestClient(app) as c:
        body = c.get("/api/codex/status").json()
    assert body["signed_in"] is True
    assert body["source"] == "ag2"
    assert body["account_id"] == "acc-1"
    assert isinstance(body["expires_at"], float)


def test_a_login_url_carries_the_state_the_headless_fallback_quotes_back(client):
    body = client.post("/api/codex/login_url").json()
    assert set(body) == {"ok", "auth_url", "state"}
    assert body["ok"] is True
    assert body["auth_url"].startswith("https://")
    assert body["state"]


def test_submitting_a_code_for_an_unknown_flow_is_a_400(client):
    r = client.post("/api/codex/submit", json={"state": "nope", "code": "abc"})
    assert r.status_code == 400
    assert r.json()["error"] == "unknown or expired sign-in"


def test_codex_logout_answers_the_bare_acknowledgement(client):
    body = client.post("/api/codex/logout").json()
    assert set(body) == {"ok"}
    assert isinstance(body["ok"], bool)


# ---- the reload every install-wide write owes the running profiles ----


def test_saving_a_key_reloads_every_runtime_so_the_next_turn_sees_it(paths):
    """The routes moved into three modules but kept one shared `reload_all`; this
    is the behaviour that would silently rot if a module grew its own copy."""
    manager = make_manager(paths)
    reloaded: list[str] = []
    original = manager.reload

    async def record(pid):
        reloaded.append(pid)
        return await original(pid)

    manager.reload = record
    from assistant.gateway.app import create_app
    from assistant.profiles import ProfileRegistry

    meta = ProfileRegistry(paths).create_profile("Test", "#109e91")
    paths.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(manager, llm_probe=_probe_ok)) as c:
        assert c.post("/api/secrets/key", json={"provider": "openai", "value": "sk-9"}).json() == {
            "ok": True
        }
    assert reloaded == [meta.id]
    assert SecretStore(paths).status({}).get("openai", {}).get("set") is True
