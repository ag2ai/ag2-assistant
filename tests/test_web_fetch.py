"""The local web fetcher: public addresses only, checked and pinned per connection."""

import asyncio
import threading
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from ag2.context import ConversationContext
from ag2.events import BuiltinToolCallEvent, ToolCallEvent, ToolResultEvent
from ag2.stream import MemoryStream
from ag2.tools import WebFetchTool

from assistant.config import Config
from assistant.tools import build_agent_tools
from assistant.tools.web_fetch import (
    DEFAULT_TIMEOUT,
    FALLBACK_TIMEOUT,
    MAX_REDIRECTS,
    MAX_TIMEOUT,
    NativeWebFetch,
    PinnedTransport,
    fallback_web_fetch_tool,
    fetch_page,
    fetch_url,
    is_public,
    local_fetch_tools,
    private_fallback_web_fetch_tool,
    private_fetch_page,
    private_web_fetch_tool,
    web_fetch_tool,
)

LOOPBACK = "127.0.0.1"


def resolver(answers: dict[str, list[str]]):
    """Resolve each host to its scripted answers, keeping the hosts asked for."""
    asked: list[str] = []

    def resolve(host: str, port: int) -> list[str]:
        asked.append(host)
        return answers[host]

    resolve.asked = asked  # type: ignore[attr-defined]
    return resolve


def loopback_only(address: str) -> bool:
    """Admit only the test server's address."""
    return address == LOOPBACK


class Site(BaseHTTPRequestHandler):
    """Names served from one address: a cookie, a redirect and a page."""

    seen: list[tuple[str, str, str | None]] = []

    def do_GET(self):  # noqa: N802
        host = self.headers["Host"].split(":")[0]
        Site.seen.append((host, self.path, self.headers.get("Cookie")))
        port = self.server.server_address[1]
        if self.path == "/login":
            target = self.headers.get("X-Next") or f"http://other.test:{port}/check"
            self.send_response(302)
            self.send_header("Set-Cookie", "sid=secret; Path=/")
            self.send_header("Location", target)
            self.end_headers()
            return
        if self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
            return
        if self.path.startswith("/to/"):
            self.send_response(302)
            self.send_header("Location", "http://" + self.path[len("/to/") :])
            self.end_headers()
            return
        body = f"page on {host}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    Site.seen = []
    server = ThreadingHTTPServer((LOOPBACK, 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def pinned(answers: dict[str, list[str]]):
    resolve = resolver(answers)
    return PinnedTransport(resolve=resolve, allow=loopback_only), resolve


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
def test_non_public_addresses_are_not_public(address):
    assert not is_public(address)


@pytest.mark.parametrize("address", ["93.184.215.14", "8.8.8.8", "2606:4700::1111"])
def test_public_addresses_are_public(address):
    assert is_public(address)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1"])
def test_the_default_policy_refuses_a_name_resolving_to_a_non_public_address(address):
    resolve = resolver({"inside.test": [address]})
    result = fetch_url("http://inside.test/api", transport=PinnedTransport(resolve=resolve))
    assert result == (
        f"Error fetching http://inside.test/api: refused, inside.test is a private or local"
        f" address ({address})"
    )


def test_the_default_policy_refuses_a_loopback_literal(site):
    result = fetch_url(f"http://127.0.0.1:{site}/page")
    assert "refused" in result
    assert Site.seen == []


def test_one_refused_answer_among_allowed_ones_refuses_the_host(site):
    transport, _ = pinned({"mixed.test": [LOOPBACK, "10.0.0.9"]})
    result = fetch_url(f"http://mixed.test:{site}/page", transport=transport)
    assert "refused" in result and "10.0.0.9" in result
    assert Site.seen == []


def test_the_connection_goes_to_the_checked_answer_under_the_requested_name(site):
    """`.test` names resolve nowhere else, so reaching the server proves the pin."""
    transport, resolve = pinned({"pinned.test": [LOOPBACK]})
    result = fetch_url(f"http://pinned.test:{site}/page", transport=transport)
    assert "page on pinned.test" in result
    assert Site.seen == [("pinned.test", "/page", None)]
    assert resolve.asked == ["pinned.test"]


def test_a_cookie_stays_with_the_host_that_set_it_across_a_same_address_redirect(site):
    transport, _ = pinned({"first.test": [LOOPBACK], "other.test": [LOOPBACK]})
    result = fetch_url(f"http://first.test:{site}/login", transport=transport)
    assert "page on other.test" in result
    assert Site.seen == [("first.test", "/login", None), ("other.test", "/check", None)]


def test_the_cookie_is_still_sent_back_to_its_own_host(site):
    """The jar works at all, so the isolation test above is not passing vacuously."""
    transport, _ = pinned({"first.test": [LOOPBACK]})

    class Next(httpx.BaseTransport):
        def handle_request(self, request):
            request.headers["X-Next"] = f"http://first.test:{site}/check"
            return transport.handle_request(request)

    fetch_url(f"http://first.test:{site}/login", transport=Next())
    assert Site.seen[-1] == ("first.test", "/check", "sid=secret")


def test_a_redirect_to_a_refused_address_is_refused(site):
    transport, _ = pinned({"public.test": [LOOPBACK], "inside.test": ["10.0.0.7"]})
    result = fetch_url(f"http://public.test:{site}/to/inside.test:{site}/page", transport=transport)
    assert "refused" in result and "10.0.0.7" in result
    assert [host for host, _, _ in Site.seen] == ["public.test"]


def test_an_unreachable_answer_falls_back_to_the_next_checked_one(site):
    """The server listens on IPv4 only, so ``::1`` refuses and the next answer serves."""
    resolve = resolver({"two.test": ["::1", LOOPBACK]})
    transport = PinnedTransport(resolve=resolve, allow={"::1", LOOPBACK}.__contains__)
    result = fetch_url(f"http://two.test:{site}/page", transport=transport)
    assert "page on two.test" in result


def test_a_name_that_does_not_resolve_is_reported():
    def resolve(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    result = fetch_url("http://nowhere.test/", transport=PinnedTransport(resolve=resolve))
    assert (
        result == "Error fetching http://nowhere.test/: cannot resolve nowhere.test: no such host"
    )


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://files.example/x"])
def test_only_http_and_https_are_fetched(url):
    sent: list[httpx.Request] = []
    result = fetch_url(url, transport=httpx.MockTransport(lambda r: sent.append(r)))
    assert result == f"Error fetching {url}: refused, only http and https URLs can be fetched"
    assert sent == []


def page(text: str = "hello"):
    return lambda request: httpx.Response(200, text=text, headers={"content-type": "text/plain"})


def test_redirects_stop_after_the_limit():
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(302, headers={"location": "/again"})

    result = fetch_url("http://loop.example/", transport=httpx.MockTransport(respond))
    assert result.startswith("Error fetching http://loop.example/:")
    assert "redirects" in result.lower()
    assert len(sent) == MAX_REDIRECTS + 1


def test_an_error_status_is_reported():
    result = fetch_url(
        "http://site.example/missing", transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    assert result == "Error fetching http://site.example/missing: HTTP 404 Not Found"


def test_web_fetch_timeout_is_the_agents_to_choose_within_bounds():
    """Absent or unusable values take the 30 s default; any value is clamped to 1-30."""
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return page()(request)

    for timeout in (5, DEFAULT_TIMEOUT, "2.5", 0, 10_000, "soon", None):
        assert "hello" in fetch_url(
            "https://x.example", timeout=timeout, transport=httpx.MockTransport(respond)
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

    slow = httpx.MockTransport(respond)
    short = fetch_url("https://slow.example", timeout=3, transport=slow)
    assert short.startswith("Error fetching https://slow.example: no response within 3s")
    assert "larger timeout, up to 30" in short
    assert fetch_url("https://slow.example", timeout=30, transport=slow) == (
        "Error fetching https://slow.example: no response within 30s"
    )


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


# --- tools.web_fetch_allow_private ---


@pytest.mark.parametrize("allow_private", [False, True])
def test_the_switch_decides_whether_a_loopback_server_is_reached(site, allow_private):
    _, _, fetcher = local_fetch_tools(allow_private)
    result = fetcher(f"http://127.0.0.1:{site}/page")
    if allow_private:
        assert "page on 127.0.0.1" in result
        assert Site.seen == [("127.0.0.1", "/page", None)]
    else:
        assert result == (
            f"Error fetching http://127.0.0.1:{site}/page: refused, 127.0.0.1 is a private or"
            " local address (127.0.0.1)"
        )
        assert Site.seen == []


def test_with_the_switch_on_a_redirect_still_opens_a_checked_connection(site):
    """Each hop still goes through the backend: a new host is resolved and connected."""
    resolve = resolver({"first.test": [LOOPBACK], "second.test": [LOOPBACK]})
    transport = PinnedTransport(resolve=resolve, allow=lambda address: True)
    result = fetch_url(f"http://first.test:{site}/to/second.test:{site}/page", transport=transport)
    assert "page on second.test" in result
    assert resolve.asked == ["first.test", "second.test"]


def test_with_the_switch_on_a_redirect_off_http_is_still_refused(site):
    _, _, fetcher = local_fetch_tools(True)
    result = fetcher(f"http://127.0.0.1:{site}/to-file")
    assert result.startswith(f"Error fetching http://127.0.0.1:{site}/to-file:")
    assert "root:" not in result
    assert Site.seen == [("127.0.0.1", "/to-file", None)]


@pytest.mark.parametrize("allow_private", [False, True])
def test_the_switch_keeps_the_scheme_check(allow_private):
    _, _, fetcher = local_fetch_tools(allow_private)
    assert fetcher("file:///etc/passwd") == (
        "Error fetching file:///etc/passwd: refused, only http and https URLs can be fetched"
    )


def test_the_agent_gets_the_fetchers_the_profile_config_chooses(paths, site):
    config = Config.for_paths(paths)
    assert web_fetch_tool in build_agent_tools("gemini", config=config)
    config.tools.web_fetch_allow_private = True
    tools = build_agent_tools("gemini", config=config)
    assert private_web_fetch_tool in tools and web_fetch_tool not in tools

    native = build_agent_tools("anthropic", builtin={"web_fetch": {}}, config=config)
    (wrapper,) = [t for t in native if isinstance(t, NativeWebFetch)]
    assert private_fallback_web_fetch_tool in native
    assert fallback_web_fetch_tool not in native
    assert "page on 127.0.0.1" in wrapper._fetcher(f"http://127.0.0.1:{site}/page", timeout=5)


async def test_the_private_tools_offer_the_model_the_same_schema():
    context = ConversationContext(stream=MemoryStream())
    for default, private in [
        (web_fetch_tool, private_web_fetch_tool),
        (fallback_web_fetch_tool, private_fallback_web_fetch_tool),
    ]:
        assert await private.schemas(context) == await default.schemas(context)


@pytest.mark.parametrize(
    ("page_fetcher", "reached"), [(fetch_page, False), (private_fetch_page, True)]
)
def test_the_fetch_page_fallback_follows_the_same_switch(site, page_fetcher, reached):
    result = page_fetcher(f"http://127.0.0.1:{site}/page")
    assert ("page on 127.0.0.1" in result) is reached
    assert ("private or local address" in result) is not reached
