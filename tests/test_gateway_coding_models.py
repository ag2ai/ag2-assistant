"""The /api/coding/{agent}/models route feeding the Settings model picker."""

import pytest

from assistant.coding import model_catalog


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(model_catalog, "_cache", {})
    monkeypatch.setattr(model_catalog, "_inflight", {})


def test_unknown_agent_is_404(profile_app):
    client, _pid = profile_app
    r = client.get("/api/coding/gemini/models")
    assert r.status_code == 404
    assert r.json()["ok"] is False


def test_catalog_served(profile_app, monkeypatch):
    client, _pid = profile_app

    async def fake_list(agent, refresh=False):
        return [model_catalog.CatalogModel(f"{agent}-m", "M", "d")], f"{agent}-m"

    monkeypatch.setattr(model_catalog, "unavailable_reason", lambda agent: "")
    monkeypatch.setattr(model_catalog, "list_models", fake_list)
    body = client.get("/api/coding/codex/models").json()
    assert body == {
        "models": [{"id": "codex-m", "name": "M", "description": "d"}],
        "current": "codex-m",
        "reason": "",
    }


def test_missing_adapter_reports_reason_without_probing(profile_app, monkeypatch):
    client, _pid = profile_app
    probed = {"n": 0}

    async def fake_list(agent, refresh=False):
        probed["n"] += 1
        return [], ""

    monkeypatch.setattr(model_catalog, "unavailable_reason", lambda agent: "adapter_missing")
    monkeypatch.setattr(model_catalog, "list_models", fake_list)
    body = client.get("/api/coding/claude/models").json()
    # The form needs to tell the user WHY there's no list — and a probe that cannot
    # work must not be paid for at all.
    assert body == {"models": [], "current": "", "reason": "adapter_missing"}
    assert probed["n"] == 0


def test_probe_failure_reads_as_probe_failed(profile_app, monkeypatch):
    client, _pid = profile_app

    async def boom(agent, refresh=False):
        raise RuntimeError("adapter closed the pipe")

    monkeypatch.setattr(model_catalog, "unavailable_reason", lambda agent: "")
    monkeypatch.setattr(model_catalog, "list_models", boom)
    body = client.get("/api/coding/codex/models").json()
    assert body == {"models": [], "current": "", "reason": "probe_failed"}


def test_refresh_query_param_reaches_the_catalog(profile_app, monkeypatch):
    client, _pid = profile_app
    seen = []

    async def fake_list(agent, refresh=False):
        seen.append(refresh)
        return [model_catalog.CatalogModel("m", "M", "")], "m"

    monkeypatch.setattr(model_catalog, "unavailable_reason", lambda agent: "")
    monkeypatch.setattr(model_catalog, "list_models", fake_list)
    client.get("/api/coding/codex/models")
    client.get("/api/coding/codex/models?refresh=1")
    assert seen == [False, True]
