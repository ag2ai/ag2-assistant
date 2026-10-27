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
"""

import asyncio
from collections.abc import Iterable
from contextlib import AsyncExitStack, ExitStack

import httpx
from ag2 import tool
from ag2.annotations import Context
from ag2.events import BuiltinToolCallEvent, ToolCallEvent, ToolResultEvent
from ag2.middleware import BaseMiddleware
from ag2.tools.tool import Tool
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = 30.0
# The budget for a fetch the provider should have run but handed back to us: the
# native path was the first try, so the second one is kept short.
FALLBACK_TIMEOUT = 10.0
FALLBACK_TOOL_NAME = "fetch_page"
# The ceiling an agent-supplied timeout is clamped to: a fetch must always come
# back, and a slow site is better reported than waited on for a whole turn.
MAX_TIMEOUT = 120.0


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
        timeout: Seconds to wait for the page (default 30, clamped to 1-120). Pass
            a shorter value for a quick look, a longer one for a site you know
            is slow; a fetch that outlasts it is reported, never waited on.

    Returns:
        The extracted text content from the page.
    """
    timeout = _bounded_timeout(timeout)
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "AG2 Assistant/0.1"},
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        return (
            f"Error fetching {url}: no response within {timeout:g}s"
            " (pass a larger timeout to wait longer)"
        )
    except httpx.HTTPError as e:
        return f"Error fetching {url}: {e}"

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
        timeout: Seconds to wait for the page (default 10, clamped to 1-120).

    Returns:
        The extracted text content from the page.
    """
    return web_fetch(url, max_chars=max_chars, timeout=timeout)


fallback_web_fetch_tool = tool(fetch_page)


class NativeWebFetch(Tool):
    """The provider's ``web_fetch`` first; ours behind it (module docstring)."""

    __slots__ = ("_native", "_timeout", "name")

    def __init__(self, native: Tool, *, timeout: float = FALLBACK_TIMEOUT) -> None:
        self._native = native
        self._timeout = timeout
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

        stack.enter_context(
            context.stream.where(ToolCallEvent.name == self.name).sub_scope(fallback)
        )

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
        return await asyncio.to_thread(web_fetch, url, max_chars=max_chars, timeout=timeout)


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
