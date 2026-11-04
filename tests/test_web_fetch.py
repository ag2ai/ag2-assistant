"""The local web fetcher: public addresses only, checked on every hop and pinned."""

import asyncio
from contextlib import ExitStack

import httpx
import pytest
from ag2.context import ConversationContext
from ag2.events import BuiltinToolCallEvent, ToolCallEvent, ToolResultEvent
from ag2.stream import MemoryStream
from ag2.tools import WebFetchTool

from assistant.tools.web_fetch import (
    DEFAULT_TIMEOUT,
    FALLBACK_TIMEOUT,
    MAX_REDIRECTS,
    MAX_TIMEOUT,
    NativeWebFetch,
    fetch_url,
    is_public,
)
from tests.support.http import client

PUBLIC = "93.184.215.14"
OTHER_PUBLIC = "8.8.8.8"


def resolver(answers: dict[str, list[str]]):
    """Resolve each host to its scripted answers; a list of lists answers in turn."""
    asked: list[str] = []

    def resolve(host: str, port: int) -> list[str]:
        asked.append(host)
        answer = answers[host]
        if answer and isinstance(answer[0], list):
            return answer.pop(0)
        return answer

    resolve.asked = asked  # type: ignore[attr-defined]
    return resolve


def recording(respond):
    """A handler that keeps every request it was sent."""
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return respond(request)

    return handle, sent


def page(text: str = "hello"):
    return lambda request: httpx.Response(200, text=text, headers={"content-type": "text/plain"})


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "::",
        "fe80::1%en0",
        "fc00::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a01:203",
        "2002:7f00:1::",
    ],
)
def test_a_host_that_resolves_to_a_non_public_address_is_refused_unsent(address):
    handle, sent = recording(page())
    result = fetch_url(
        "http://inside.example/api",
        client=client(handle),
        resolve=resolver({"inside.example": [address]}),
    )
    assert result.startswith("Error fetching http://inside.example/api: refused,")
    assert address in result
    assert sent == []


@pytest.mark.parametrize("address", [PUBLIC, OTHER_PUBLIC, "2606:4700::1111"])
def test_public_addresses_are_public(address):
    assert is_public(address)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8800/api/health", "http://[::1]:8800/"])
def test_an_address_literal_is_checked_like_a_name(url):
    handle, sent = recording(page())
    host = httpx.URL(url).host
    result = fetch_url(url, client=client(handle), resolve=resolver({host: [host]}))
    assert "refused" in result
    assert sent == []


def test_one_private_answer_among_public_ones_refuses_the_host():
    handle, sent = recording(page())
    result = fetch_url(
        "https://mixed.example/",
        client=client(handle),
        resolve=resolver({"mixed.example": [PUBLIC, "127.0.0.1"]}),
    )
    assert "refused" in result and "127.0.0.1" in result
    assert sent == []


def test_a_public_host_is_fetched_from_the_checked_address_under_its_own_name():
    handle, sent = recording(page("the page"))
    result = fetch_url(
        "https://site.example:8443/path?q=1",
        client=client(handle),
        resolve=resolver({"site.example": [PUBLIC]}),
    )
    assert "the page" in result
    [request] = sent
    assert request.url.host == PUBLIC
    assert request.url.port == 8443 and request.url.raw_path == b"/path?q=1"
    assert request.headers["host"] == "site.example:8443"
    assert request.extensions["sni_hostname"] == "site.example"


def test_plain_http_carries_no_tls_name():
    handle, sent = recording(page())
    fetch_url(
        "http://site.example/",
        client=client(handle),
        resolve=resolver({"site.example": [PUBLIC]}),
    )
    [request] = sent
    assert request.url.host == PUBLIC
    assert "sni_hostname" not in request.extensions


def test_the_connection_uses_the_answer_that_was_checked_not_a_later_one():
    """A name re-resolved after the check (DNS rebinding) never picks the address."""
    handle, sent = recording(page())
    resolve = resolver({"rebind.example": [[PUBLIC], ["127.0.0.1"]]})
    fetch_url("http://rebind.example/", client=client(handle), resolve=resolve)
    assert [r.url.host for r in sent] == [PUBLIC]
    assert resolve.asked == ["rebind.example"]


def test_a_redirect_to_a_private_address_is_refused():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8800/api/health"})

    handle, sent = recording(respond)
    result = fetch_url(
        "https://public.example/go",
        client=client(handle),
        resolve=resolver({"public.example": [PUBLIC], "127.0.0.1": ["127.0.0.1"]}),
    )
    assert "refused" in result and "127.0.0.1" in result
    assert [r.url.host for r in sent] == [PUBLIC]


def test_every_hop_is_resolved_again_so_a_rebound_redirect_is_refused():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/next"})

    handle, sent = recording(respond)
    resolve = resolver({"flip.example": [[PUBLIC], ["10.0.0.7"]]})
    result = fetch_url("http://flip.example/start", client=client(handle), resolve=resolve)
    assert "refused" in result and "10.0.0.7" in result
    assert len(sent) == 1
    assert resolve.asked == ["flip.example", "flip.example"]


def test_a_redirect_to_a_public_address_is_followed_and_joined():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "first.example":
            return httpx.Response(301, headers={"location": "https://second.example/final"})
        return page("arrived")(request)

    handle, sent = recording(respond)
    result = fetch_url(
        "http://first.example/",
        client=client(handle),
        resolve=resolver({"first.example": [PUBLIC], "second.example": [OTHER_PUBLIC]}),
    )
    assert "arrived" in result
    assert [(r.url.host, r.headers["host"], r.url.path) for r in sent] == [
        (PUBLIC, "first.example", "/"),
        (OTHER_PUBLIC, "second.example", "/final"),
    ]


def test_redirects_stop_after_the_limit():
    handle, sent = recording(lambda request: httpx.Response(302, headers={"location": "/again"}))
    result = fetch_url(
        "http://loop.example/",
        client=client(handle),
        resolve=resolver({"loop.example": [PUBLIC]}),
    )
    assert result.startswith("Error fetching http://loop.example/:")
    assert "redirects" in result
    assert len(sent) == MAX_REDIRECTS + 1


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://files.example/x"])
def test_only_http_and_https_are_fetched(url):
    handle, sent = recording(page())
    result = fetch_url(url, client=client(handle), resolve=resolver({}))
    assert result == f"Error fetching {url}: refused, only http and https URLs can be fetched"
    assert sent == []


def test_an_unreachable_address_falls_back_to_the_next_checked_one():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == PUBLIC:
            raise httpx.ConnectError("unreachable", request=request)
        return page("second address")(request)

    handle, sent = recording(respond)
    result = fetch_url(
        "http://two.example/",
        client=client(handle),
        resolve=resolver({"two.example": [PUBLIC, OTHER_PUBLIC]}),
    )
    assert "second address" in result
    assert [r.url.host for r in sent] == [PUBLIC, OTHER_PUBLIC]


def test_a_name_that_does_not_resolve_is_reported():
    def resolve(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    handle, sent = recording(page())
    result = fetch_url("http://nowhere.example/", client=client(handle), resolve=resolve)
    assert (
        result
        == "Error fetching http://nowhere.example/: cannot resolve nowhere.example: no such host"
    )
    assert sent == []


def test_an_error_status_is_reported():
    handle, _ = recording(lambda request: httpx.Response(404))
    result = fetch_url(
        "http://site.example/missing",
        client=client(handle),
        resolve=resolver({"site.example": [PUBLIC]}),
    )
    assert result == "Error fetching http://site.example/missing: HTTP 404 Not Found"


def test_web_fetch_timeout_is_the_agents_to_choose_within_bounds():
    """Absent or unusable values take the 30 s default; any value is clamped to 1-30."""
    handle, sent = recording(page())
    resolve = resolver({"x.example": [PUBLIC]})
    for timeout in (5, DEFAULT_TIMEOUT, "2.5", 0, 10_000, "soon", None):
        assert "hello" in fetch_url(
            "https://x.example", timeout=timeout, client=client(handle), resolve=resolve
        )
    assert [r.extensions["timeout"]["read"] for r in sent] == [
        5.0,
        DEFAULT_TIMEOUT,
        2.5,
        1.0,
        MAX_TIMEOUT,
        DEFAULT_TIMEOUT,
        DEFAULT_TIMEOUT,
    ]


def test_web_fetch_reports_a_timeout_instead_of_failing_the_turn():
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    resolve = resolver({"slow.example": [PUBLIC]})
    short = fetch_url("https://slow.example", timeout=3, client=client(respond), resolve=resolve)
    assert short.startswith("Error fetching https://slow.example: no response within 3s")
    assert "larger timeout, up to 30" in short
    assert fetch_url(
        "https://slow.example", timeout=30, client=client(respond), resolve=resolve
    ) == ("Error fetching https://slow.example: no response within 30s")


async def test_a_native_fetch_handed_back_to_the_client_runs_ours_on_a_short_budget():
    """A call that reaches us as a client-side tool call runs our fetcher, 10 s by
    default and the agent's `timeout` when given; a provider-run call is left alone."""
    seen = []

    def fetcher(url, *, max_chars, timeout):
        seen.append((url, timeout))
        return "fetched locally"

    wrapper = NativeWebFetch(WebFetchTool(max_uses=2), fetcher=fetcher)
    stream = MemoryStream()
    context = ConversationContext(stream=stream)
    (schema,) = await wrapper.schemas(context)
    assert type(schema).__name__ == "WebFetchToolSchema" and schema.max_uses == 2

    results = []
    stream.where(ToolResultEvent).subscribe(lambda ev: results.append(ev))
    with ExitStack() as stack:
        wrapper.register(stack, context)
        await context.send(
            ToolCallEvent(id="c1", name="web_fetch", arguments='{"url": "https://x.invalid"}')
        )
        await context.send(
            ToolCallEvent(
                id="c2", name="web_fetch", arguments='{"url": "https://y.invalid", "timeout": 4}'
            )
        )
        await context.send(
            BuiltinToolCallEvent(
                id="c3", name="web_fetch", arguments='{"url": "https://z.invalid"}'
            )
        )
        await context.send(ToolCallEvent(id="c4", name="web_fetch", arguments="{}"))
        await context.send(ToolCallEvent(id="c5", name="other_tool", arguments="{}"))
        for _ in range(4):
            await asyncio.sleep(0)

    assert seen == [("https://x.invalid", FALLBACK_TIMEOUT), ("https://y.invalid", 4)]
    by_id = {r.parent_id: r for r in results}
    assert set(by_id) == {"c1", "c2", "c4"}
    assert "fetched locally" in by_id["c1"].result.parts[0].content
    assert by_id["c4"].result.parts[0].content == "Error fetching: web_fetch needs a url"


def test_the_native_wrapper_runs_the_guarded_fetcher_by_default():
    result = NativeWebFetch(WebFetchTool())._fetcher("http://127.0.0.1:8800/", timeout=1)
    assert result.startswith("Error fetching http://127.0.0.1:8800/: refused,")
