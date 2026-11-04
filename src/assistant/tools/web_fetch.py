"""Web fetch tool — fetches and extracts readable content from URLs.

Uses httpx for fetching and BeautifulSoup for content extraction.

Two shapes ride on it:

- ``web_fetch_tool`` — the plain function tool, served when no provider-native
  fetcher is switched on.
- ``NativeWebFetch`` — the provider's own ``web_fetch`` (Anthropic's server-side
  fetcher, Gemini's url_context) tried FIRST, with ours behind it. The provider
  runs the fetch on its side and we never see the call; but a call that reaches
  us as an ordinary client-side tool call means the provider did not run it (a
  proxy that forwards the model's output verbatim, a model served without the
  server tool), and without this the turn waited forever for a result nobody
  owed. That call runs our fetcher with a short budget (``FALLBACK_TIMEOUT``)
  instead. ``fallback_web_fetch_tool`` is the same fetcher under its own name,
  offered beside the native one so the model can retry locally when the
  provider's fetch itself failed (blocked domain, error result).

Every hop, redirects included, resolves its host first and refuses any address
that is not public; the request then connects to the checked address itself.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Iterable
from contextlib import AsyncExitStack, ExitStack

import httpx
from ag2 import tool
from ag2.annotations import Context
from ag2.events import BuiltinToolCallEvent, ToolCallEvent, ToolResultEvent
from ag2.events.conditions import OpCondition, check_eq
from ag2.middleware import BaseMiddleware
from ag2.tools.tool import Tool
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = 30.0
# The budget for a fetch the provider should have run but handed back to us: the
# native path was the first try, so the second one is kept short.
FALLBACK_TIMEOUT = 10.0
FALLBACK_TOOL_NAME = "fetch_page"
# The ceiling an agent-supplied timeout is clamped to: a fetch must always come
# back within half a minute, and a slow site is better reported than waited on.
MAX_TIMEOUT = 30.0
MAX_REDIRECTS = 10
_USER_AGENT = "AG2 Assistant/0.1"
# NAT64's well-known prefix carries an IPv4 address in its last 32 bits.
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")

Resolve = Callable[[str, int], list[str]]


class RefusedAddress(Exception):
    """A URL whose host is not a public internet address."""


def resolve_host(host: str, port: int) -> list[str]:
    """The addresses ``host`` resolves to, in the system's preferred order."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_ADDRCONFIG)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public(address: str) -> bool:
    """Whether ``address`` is a global unicast address, judging NAT64 by its IPv4 part."""
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64:
        ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global and not ip.is_multicast


def _checked_addresses(url: httpx.URL, resolve: Resolve) -> list[str]:
    """The public addresses to connect to for ``url``; raises when any is not public."""
    if url.scheme not in ("http", "https"):
        raise RefusedAddress("only http and https URLs can be fetched")
    host = url.host
    if not host:
        raise RefusedAddress("the URL has no host")
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        addresses = resolve(host, port)
    except OSError as exc:
        raise httpx.ConnectError(f"cannot resolve {host}: {exc}") from exc
    if not addresses:
        raise httpx.ConnectError(f"cannot resolve {host}")
    for address in addresses:
        if not is_public(address):
            raise RefusedAddress(f"{host} is a private or local address ({address})")
    return addresses


def _send_pinned(
    client: httpx.Client, url: httpx.URL, addresses: list[str], timeout: float
) -> httpx.Response:
    """GET ``url`` from the first reachable checked address, keeping its name for Host and TLS."""
    headers = {"User-Agent": _USER_AGENT, "Host": url.netloc.decode("ascii")}
    extensions = {"sni_hostname": url.host} if url.scheme == "https" else {}
    error: httpx.ConnectError | None = None
    for address in addresses:
        request = client.build_request(
            "GET",
            url.copy_with(host=address),
            headers=headers,
            extensions=extensions,
            timeout=timeout,
        )
        try:
            return client.send(request, follow_redirects=False)
        except httpx.ConnectError as exc:
            error = exc
    assert error is not None
    raise error


def _get(client: httpx.Client, url: str, timeout: float, resolve: Resolve) -> httpx.Response:
    """GET ``url``, following redirects, with every hop checked and pinned."""
    target = httpx.URL(url)
    for _ in range(MAX_REDIRECTS + 1):
        response = _send_pinned(client, target, _checked_addresses(target, resolve), timeout)
        if not response.has_redirect_location:
            return response
        target = target.join(response.headers["location"])
        response.close()
    raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")


def _bounded_timeout(timeout) -> float:
    try:
        seconds = float(timeout)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if seconds != seconds:  # NaN
        return DEFAULT_TIMEOUT
    return min(max(seconds, 1.0), MAX_TIMEOUT)


def web_fetch(url: str, max_chars: int = 10000, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Fetch a web page and extract its readable text content.

    Args:
        url: The URL to fetch.
        max_chars: Maximum characters to return (default 10000).
        timeout: Seconds to wait for the page (default and ceiling 30, at least 1).
            Pass a shorter value for a quick look; a fetch that outlasts it is
            reported as the result, never waited on.

    Returns:
        The extracted text content from the page.
    """
    return fetch_url(url, max_chars, timeout)


def fetch_url(
    url: str,
    max_chars: int = 10000,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    client: httpx.Client | None = None,
    resolve: Resolve = resolve_host,
) -> str:
    """``web_fetch`` over an injectable client and resolver."""
    if client is None:
        with httpx.Client(trust_env=False) as owned:
            return fetch_url(url, max_chars, timeout, client=owned, resolve=resolve)
    timeout = _bounded_timeout(timeout)
    try:
        response = _get(client, url, timeout, resolve)
    except RefusedAddress as e:
        return f"Error fetching {url}: refused, {e}"
    except httpx.TimeoutException:
        hint = " (pass a larger timeout, up to 30, to wait longer)" if timeout < MAX_TIMEOUT else ""
        return f"Error fetching {url}: no response within {timeout:g}s{hint}"
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        return f"Error fetching {url}: {e}"
    if response.is_error:
        return f"Error fetching {url}: HTTP {response.status_code} {response.reason_phrase}"

    content_type = response.headers.get("content-type", "")

    if "application/json" in content_type:
        text = response.text[:max_chars]
        return f"JSON from {url}:\n\n{text}"

    if "text/plain" in content_type:
        text = response.text[:max_chars]
        return f"Content from {url}:\n\n{text}"

    # HTML — extract readable content
    soup = BeautifulSoup(response.text, "html.parser")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    # Try to find main content
    main = soup.find("main") or soup.find("article") or soup.find("body")
    if main is None:
        main = soup

    text = main.get_text(separator="\n", strip=True)

    # Clean up whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[Content truncated]"

    title = soup.title.string.strip() if soup.title and soup.title.string else url
    return f"# {title}\n\nSource: {url}\n\n{text}"


web_fetch_tool = tool(web_fetch)


def fetch_page(url: str, max_chars: int = 10000, timeout: float = FALLBACK_TIMEOUT) -> str:
    """Fetch a web page with the assistant's own fetcher and extract its readable text.

    Use this only after web_fetch failed for the URL — it was blocked, returned an
    error, or came back empty. web_fetch is always the first choice.

    Args:
        url: The URL to fetch.
        max_chars: Maximum characters to return (default 10000).
        timeout: Seconds to wait for the page (default 10, clamped to 1-30).

    Returns:
        The extracted text content from the page.
    """
    return web_fetch(url, max_chars=max_chars, timeout=timeout)


fallback_web_fetch_tool = tool(fetch_page)


class NativeWebFetch(Tool):
    """The provider's ``web_fetch`` first; ours behind it (module docstring)."""

    __slots__ = ("_fetcher", "_native", "_timeout", "name")

    def __init__(
        self,
        native: Tool,
        *,
        timeout: float = FALLBACK_TIMEOUT,
        fetcher: Callable[..., str] = web_fetch,
    ) -> None:
        self._native = native
        self._timeout = timeout
        self._fetcher = fetcher
        self.name = native.name

    @property
    def native(self) -> Tool:
        return self._native

    async def schemas(self, context: Context):
        return await self._native.schemas(context)

    def register(
        self,
        stack: "ExitStack | AsyncExitStack",
        context: Context,
        *,
        middleware: Iterable[BaseMiddleware] = (),
    ) -> None:
        self._native.register(stack, context, middleware=middleware)

        async def fallback(event: ToolCallEvent, context: Context) -> None:
            if isinstance(event, BuiltinToolCallEvent):
                return  # the provider ran it; its result rides the model's reply
            await context.send(ToolResultEvent.from_call(event, await self._fetch(event)))

        # The `ToolCallEvent.name == self.name` descriptor form builds this exact
        # condition, but reads to a type checker as a plain bool comparison.
        calls_this_tool = OpCondition(check_eq, "name", self.name, ToolCallEvent)
        stack.enter_context(context.stream.where(calls_this_tool).sub_scope(fallback))

    async def _fetch(self, event: ToolCallEvent) -> str:
        try:
            args = dict(event.serialized_arguments)
        except (TypeError, ValueError):
            args = {}
        url = args.get("url")
        if not isinstance(url, str) or not url:
            return "Error fetching: web_fetch needs a url"
        max_chars = args.get("max_chars", 10000)
        if not isinstance(max_chars, int) or max_chars < 1:
            max_chars = 10000
        timeout = args.get("timeout", self._timeout)
        return await asyncio.to_thread(self._fetcher, url, max_chars=max_chars, timeout=timeout)


def native_first(tools: list, *, timeout: float = FALLBACK_TIMEOUT) -> list:
    """Wrap a provider-native ``web_fetch`` in ``NativeWebFetch`` and offer the local
    fallback beside it; a list without one is returned unchanged."""
    out, wrapped = [], False
    for t in tools:
        if getattr(t, "name", "") == "web_fetch" and not isinstance(t, NativeWebFetch):
            t = NativeWebFetch(t, timeout=timeout)
        wrapped = wrapped or isinstance(t, NativeWebFetch)
        out.append(t)
    if wrapped and not any(getattr(t, "name", "") == FALLBACK_TOOL_NAME for t in out):
        out.append(fallback_web_fetch_tool)
    return out
