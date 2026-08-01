"""The production provider probe: what an endpoint's answer is made of.

The whole httpx stack runs — only the socket is scripted (tests/support/http.py),
so request building, headers and response parsing are the real ones.
"""

import httpx
import pytest

from assistant.provider_catalog import (
    CatalogTarget,
    CatalogUnavailable,
    probe_provider_models,
)
from tests.support.http import (
    async_client,
    failing_responder,
    json_responder,
    recording_responder,
    unreachable_responder,
)

TAGS = {"models": [{"name": "llama3.2:latest"}, {"name": "qwen3:8b"}]}


async def _probe(handler, **target):
    async with async_client(handler) as client:
        return await probe_provider_models(CatalogTarget(type="ollama", **target), client=client)


async def test_the_tags_pulled_on_that_host_are_the_catalog():
    assert await _probe(json_responder(TAGS)) == ["llama3.2:latest", "qwen3:8b"]


async def test_the_host_asked_is_the_one_configured():
    handler, sent = recording_responder(TAGS)
    await _probe(handler, host="http://box:11434/")
    assert [s["url"] for s in sent] == ["http://box:11434/api/tags"]


async def test_no_host_falls_back_to_the_local_daemon():
    handler, sent = recording_responder(TAGS)
    await _probe(handler)
    assert [s["url"] for s in sent] == ["http://localhost:11434/api/tags"]


async def test_a_host_with_nothing_pulled_answers_an_empty_catalog():
    assert await _probe(json_responder({"models": []})) == []


async def test_a_dead_host_is_unreachable():
    with pytest.raises(CatalogUnavailable) as caught:
        await _probe(unreachable_responder())
    assert caught.value.reason == "unreachable"


async def test_an_endpoint_answering_something_else_publishes_no_list():
    with pytest.raises(CatalogUnavailable) as caught:
        await _probe(json_responder({"hello": "i am not ollama"}))
    assert caught.value.reason == "no_list_endpoint"


async def test_a_non_json_answer_publishes_no_list():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>a proxy login page</html>")

    with pytest.raises(CatalogUnavailable) as caught:
        await _probe(handle)
    assert caught.value.reason == "no_list_endpoint"


async def test_a_404_publishes_no_list_rather_than_reading_as_dead():
    with pytest.raises(CatalogUnavailable) as caught:
        await _probe(failing_responder(404))
    assert caught.value.reason == "no_list_endpoint"


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_credential_is_unauthorized(status):
    with pytest.raises(CatalogUnavailable) as caught:
        await _probe(failing_responder(status))
    assert caught.value.reason == "unauthorized"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_provider_having_a_bad_moment_is_unreachable(status):
    # "publishes no model list" is permanent and tells the user to stop waiting; a
    # rate limit or a crash is the opposite, and must not be told in those words.
    with pytest.raises(CatalogUnavailable) as caught:
        await _probe(failing_responder(status))
    assert caught.value.reason == "unreachable"


async def test_a_type_with_no_provider_list_is_not_probeable():
    async with async_client(json_responder(TAGS)) as client:
        with pytest.raises(CatalogUnavailable) as caught:
            await probe_provider_models(CatalogTarget(type="openai_subscription"), client=client)
    assert caught.value.reason == "not_probeable"


def test_a_target_never_repr_s_its_key():
    target = CatalogTarget(type="ollama", host="http://box:11434", api_key="sk-live-secret")
    assert "sk-live-secret" not in repr(target)


# ---- Keyed providers: the credential is the caller's to resolve, never the route's --


async def _keyed(handler, ctype, **target):
    async with async_client(handler) as client:
        return await probe_provider_models(
            CatalogTarget(type=ctype, api_key="sk-live", **target), client=client
        )


GEMINI_LIST = {
    "models": [
        {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]},
    ]
}
OPENAI_LIST = {"data": [{"id": "gpt-5.6-terra"}, {"id": "text-embedding-3-large"}]}
ANTHROPIC_LIST = {"data": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-4-8"}]}


async def test_gemini_names_lose_their_models_prefix():
    assert await _keyed(json_responder(GEMINI_LIST), "gemini") == ["gemini-3.6-flash"]


async def test_gemini_filters_on_the_provider_s_own_metadata_not_on_names():
    # An embeddings model is dropped because Gemini says it cannot generateContent,
    # not because its name looked like one.
    models = await _keyed(json_responder(GEMINI_LIST), "gemini")
    assert "text-embedding-005" not in models


async def test_a_gemini_model_that_declares_no_methods_is_kept():
    # Failing open: an unrecognised shape is offered, never hidden.
    payload = {"models": [{"name": "models/zeta-9"}]}
    assert await _keyed(json_responder(payload), "gemini") == ["zeta-9"]


async def test_gemini_takes_its_key_in_a_header_and_never_in_the_url():
    # A URL carrying a key reaches proxy and access logs; a header does not. The
    # browser path has no such choice, this one does.
    handler, sent = recording_responder(GEMINI_LIST)
    await _keyed(handler, "gemini")
    assert sent[0]["headers"]["x-goog-api-key"] == "sk-live"
    assert "sk-live" not in sent[0]["url"]


async def test_openai_takes_a_bearer_header():
    handler, sent = recording_responder(OPENAI_LIST)
    await _keyed(handler, "openai_responses")
    assert sent[0]["headers"]["authorization"] == "Bearer sk-live"
    assert sent[0]["url"] == "https://api.openai.com/v1/models"


async def test_the_two_openai_surfaces_read_one_catalog():
    assert await _keyed(json_responder(OPENAI_LIST), "openai") == await _keyed(
        json_responder(OPENAI_LIST), "openai_responses"
    )


async def test_anthropic_takes_its_key_and_a_version_header():
    handler, sent = recording_responder(ANTHROPIC_LIST)
    await _keyed(handler, "anthropic")
    assert sent[0]["headers"]["x-api-key"] == "sk-live"
    assert sent[0]["headers"]["anthropic-version"]
    assert sent[0]["url"] == "https://api.anthropic.com/v1/models"


async def test_anthropic_needs_no_filtering():
    assert await _keyed(json_responder(ANTHROPIC_LIST), "anthropic") == [
        "claude-sonnet-5",
        "claude-opus-4-8",
    ]


async def test_a_custom_endpoint_is_asked_at_its_own_address():
    handler, sent = recording_responder(OPENAI_LIST)
    await _keyed(handler, "openai", base_url="https://api.minimax.io/v1/")
    assert sent[0]["url"] == "https://api.minimax.io/v1/models"


async def test_a_rejected_key_is_unauthorized_on_every_keyed_type():
    for ctype in ("gemini", "openai", "openai_responses", "anthropic"):
        with pytest.raises(CatalogUnavailable) as caught:
            await _keyed(failing_responder(401), ctype)
        assert caught.value.reason == "unauthorized", ctype


async def test_a_keyed_type_with_no_key_is_never_asked():
    # No credential yet is not a failure to reach anything — it is nothing to ask with.
    async with async_client(json_responder(OPENAI_LIST)) as client:
        with pytest.raises(CatalogUnavailable) as caught:
            await probe_provider_models(CatalogTarget(type="openai"), client=client)
    assert caught.value.reason == "not_probeable"


async def test_a_keyless_custom_endpoint_is_still_asked():
    # A local server behind an OpenAI-compatible URL needs no key at all.
    handler, sent = recording_responder(OPENAI_LIST)
    async with async_client(handler) as client:
        await probe_provider_models(
            CatalogTarget(type="openai", base_url="http://localhost:8080/v1"), client=client
        )
    assert sent[0]["url"] == "http://localhost:8080/v1/models"


async def test_the_chatgpt_subscription_is_never_probeable():
    async with async_client(json_responder(OPENAI_LIST)) as client:
        with pytest.raises(CatalogUnavailable) as caught:
            await probe_provider_models(
                CatalogTarget(type="openai_subscription", api_key="sk-live"), client=client
            )
    assert caught.value.reason == "not_probeable"
