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


async def test_a_type_with_no_provider_list_is_not_probeable():
    async with async_client(json_responder(TAGS)) as client:
        with pytest.raises(CatalogUnavailable) as caught:
            await probe_provider_models(CatalogTarget(type="openai_subscription"), client=client)
    assert caught.value.reason == "not_probeable"


def test_a_target_never_repr_s_its_key():
    target = CatalogTarget(type="ollama", host="http://box:11434", api_key="sk-live-secret")
    assert "sk-live-secret" not in repr(target)
