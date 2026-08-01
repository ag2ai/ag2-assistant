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


def test_an_entry_with_no_host_is_probed_where_the_install_runs_ollama(profile_app_factory, paths):
    # A Docker install reaches Ollama at the host bridge, not at its own localhost —
    # the same address the turn would use is the one whose tags are worth listing.
    from assistant.secrets import SecretStore

    SecretStore(paths).set_key("ollama", "http://host.docker.internal:11434")
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    client.get("/api/llm-configs/models", params={"type": "ollama"})
    assert [t.host for t in seen] == ["http://host.docker.internal:11434"]


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


# ---- Keyed types: the route resolves the Secret, and takes no key from the caller --


def _with_secret(paths, provider="gemini", value="sk-saved", default=False):
    """A Secret on disk, the way Settings → Secrets would have written it."""
    from assistant.secrets import SecretStore

    store = SecretStore(paths)
    return store.create_secret(
        name=f"{provider} key", value=value, provider=provider, default=default
    )


def test_a_referenced_secret_is_what_the_probe_is_given(profile_app_factory, paths):
    secret = _with_secret(paths)
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    client.get("/api/llm-configs/models", params={"type": "gemini", "secret_id": secret["id"]})
    assert [t.api_key for t in seen] == ["sk-saved"]


def test_a_dangling_secret_reference_falls_back_like_the_request_would(profile_app_factory, paths):
    # No Secret and no default key on this install: nothing to probe with.
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    client.get("/api/llm-configs/models", params={"type": "gemini", "secret_id": "gone"})
    assert [t.api_key for t in seen] == [""]


def test_the_install_wide_key_is_what_a_config_without_a_secret_is_probed_with(
    profile_app_factory, paths
):
    # It is the key the turn itself would send, so the list must describe it —
    # otherwise the probe comes back keyless and the field says the type has no
    # model list at all, which is only ever true of the ChatGPT subscription.
    _with_secret(paths, provider="gemini", value="sk-default", default=True)
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    client.get("/api/llm-configs/models", params={"type": "gemini"})
    assert [t.api_key for t in seen] == ["sk-default"]


def test_a_custom_endpoint_is_never_handed_the_install_wide_key(profile_app_factory, paths):
    _with_secret(paths, provider="anthropic", value="sk-default", default=True)
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    client.get(
        "/api/llm-configs/models",
        params={"type": "anthropic", "base_url": "https://api.minimax.io/anthropic"},
    )
    assert [t.api_key for t in seen] == [""]


def test_a_rejected_secret_reads_as_unauthorized(profile_app_factory):
    client, _pid = _app(profile_app_factory, raises=CatalogUnavailable("unauthorized"))
    body = client.get("/api/llm-configs/models", params={"type": "gemini"}).json()
    assert body == {"models": [], "current": "", "reason": "unauthorized"}


def test_a_custom_endpoint_is_passed_through_to_the_probe(profile_app_factory):
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    client.get(
        "/api/llm-configs/models",
        params={"type": "anthropic", "base_url": "https://api.minimax.io/anthropic"},
    )
    assert [t.base_url for t in seen] == ["https://api.minimax.io/anthropic"]


def test_the_chatgpt_subscription_is_answered_without_probing_anything(profile_app_factory):
    seen = []
    client, _pid = profile_app_factory(llm_catalog_probe=_probe(seen=seen))
    r = client.get("/api/llm-configs/models", params={"type": "openai_subscription"})
    assert r.status_code == 200
    assert r.json() == {"models": [], "current": "", "reason": "not_probeable"}
    assert seen == [], "a type with no key to probe with was probed anyway"


def test_every_keyed_type_is_served_rather_than_404ed(profile_app_factory):
    client, _pid = _app(profile_app_factory, models=["a-model"])
    for ctype in ("gemini", "openai", "openai_responses", "anthropic"):
        body = client.get("/api/llm-configs/models", params={"type": ctype}).json()
        assert body["models"] == [{"id": "a-model", "name": "a-model", "description": ""}], ctype
