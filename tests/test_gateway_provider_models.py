"""The /api/llm-configs/models route feeding the Model field's combobox.

The route drives an injected provider probe (ADR 0019), so these tests hand the
app a fake one and assert on what the route makes of what it returns. Nothing is
patched and nothing reaches a network.
"""

import pytest

from assistant.provider_catalog import CatalogTarget, CatalogUnavailable


def _probe(models=(), raises=None, seen=None):
    """A provider probe that answers with ``models``, or fails with ``raises``.
    ``seen`` collects the targets it was asked about."""

    async def probe(target: CatalogTarget) -> list[str]:
        if seen is not None:
            seen.append(target)
        if raises is not None:
            raise raises
        return list(models)

    return probe


def _app(factory, **kw):
    return factory(llm_catalog_probe=_probe(**kw))


def test_a_catalog_is_served_in_the_acp_route_s_envelope(profile_app_factory):
    client, _pid = _app(profile_app_factory, models=["llama3.2:latest", "qwen3:8b"])
    body = client.get("/api/llm-configs/models", params={"type": "ollama"}).json()
    assert body == {
        "models": [
            {"id": "llama3.2:latest", "name": "llama3.2:latest", "description": ""},
            {"id": "qwen3:8b", "name": "qwen3:8b", "description": ""},
        ],
        "current": "",
        "reason": "",
    }


def test_the_host_the_user_typed_is_what_gets_asked(profile_app_factory):
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    client.get("/api/llm-configs/models", params={"type": "ollama", "host": "http://box:11434"})
    assert [t.host for t in seen] == ["http://box:11434"]


def test_an_unreachable_host_reads_as_unreachable(profile_app_factory):
    client, _pid = _app(profile_app_factory, raises=CatalogUnavailable("unreachable"))
    body = client.get("/api/llm-configs/models", params={"type": "ollama"}).json()
    assert body == {"models": [], "current": "", "reason": "unreachable"}


def test_an_endpoint_publishing_no_list_says_so(profile_app_factory):
    client, _pid = _app(profile_app_factory, raises=CatalogUnavailable("no_list_endpoint"))
    body = client.get("/api/llm-configs/models", params={"type": "ollama"}).json()
    assert body["reason"] == "no_list_endpoint"


def test_a_probe_that_blows_up_is_a_reason_not_a_500(profile_app_factory):
    client, _pid = _app(profile_app_factory, raises=RuntimeError("boom"))
    r = client.get("/api/llm-configs/models", params={"type": "ollama"})
    assert r.status_code == 200
    assert r.json()["reason"] == "unreachable"


def test_an_empty_catalog_from_a_live_host_is_not_a_failure(profile_app_factory):
    # Ollama with nothing pulled answers a real, empty list. Saying "unreachable"
    # there would send the user hunting for a network problem they do not have.
    client, _pid = _app(profile_app_factory, models=[])
    body = client.get("/api/llm-configs/models", params={"type": "ollama"}).json()
    assert body == {"models": [], "current": "", "reason": ""}


def test_a_type_with_no_provider_list_is_404(profile_app_factory):
    client, _pid = _app(profile_app_factory)
    for ctype in ("claude_code", "codex", "nonsense", ""):
        r = client.get("/api/llm-configs/models", params={"type": ctype})
        assert r.status_code == 404, ctype
        assert r.json()["ok"] is False


@pytest.mark.parametrize("param", ["api_key", "key", "secret", "token", "password"])
def test_the_route_accepts_no_key_material(profile_app_factory, param):
    # ADR 0024: a pasted key goes to the provider, never to us. The route refuses
    # rather than ignoring, so routing one through here fails loudly in review.
    client, _pid = _app(profile_app_factory)
    r = client.get("/api/llm-configs/models", params={"type": "ollama", param: "sk-live"})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_a_refresh_read_is_never_served_from_a_cache(profile_app_factory):
    client, _pid = _app(profile_app_factory, models=["llama3.2"])
    plain = client.get("/api/llm-configs/models", params={"type": "ollama"})
    fresh = client.get("/api/llm-configs/models", params={"type": "ollama", "refresh": "1"})
    assert "no-store" in fresh.headers["cache-control"]
    assert "no-store" not in plain.headers["cache-control"]
    assert plain.json() == fresh.json()


def test_every_read_probes_because_there_is_no_server_cache(profile_app_factory):
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    for _ in range(3):
        client.get("/api/llm-configs/models", params={"type": "ollama"})
    assert len(seen) == 3
