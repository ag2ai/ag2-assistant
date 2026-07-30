"""Real httpx clients over a scripted transport.

``httpx.MockTransport`` keeps the whole client stack — request building, headers,
timeouts, response parsing — and only replaces the socket. So a test drives the
same code path production does, without patching anything.
"""

import json
from collections.abc import Callable, Mapping

import httpx
from ag2.tools.skills.skill_search.client import SkillsClient

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> httpx.Client:
    """A real synchronous client whose requests are answered by ``handler``."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def async_client(handler: Handler) -> httpx.AsyncClient:
    """A real asynchronous client whose requests are answered by ``handler``."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_responder(payload: Mapping, status_code: int = 200) -> Handler:
    """Answer every request with the same JSON body and status."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=dict(payload))

    return handle


def failing_responder(status_code: int, text: str = "") -> Handler:
    """Answer every request with an error status and a plain-text body."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=text)

    return handle


def unreachable_responder(message: str = "network is down") -> Handler:
    """Fail every request at the transport layer, like a dead network would."""

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(message, request=request)

    return handle


def recording_responder(payload: Mapping, status_code: int = 200) -> tuple[Handler, list[dict]]:
    """Like :func:`json_responder`, plus a list that collects what was sent —
    ``{"url", "headers", "body"}`` per request, in order."""
    sent: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": request.content.decode() or "",
            }
        )
        return httpx.Response(status_code, json=dict(payload))

    return handle, sent


class ScriptedSkillsClient(SkillsClient):
    """ag2's skills.sh registry client with only its socket replaced.

    Search parsing, the tarball stream, the sha256 of the bytes, the archive
    extraction and ``runtime.install`` all run for real — ``handler`` just answers the
    two HTTP calls (``skills.sh/api/search`` and GitHub's tarball endpoint) the way the
    registry would.
    """

    def __init__(self, handler: Handler) -> None:
        super().__init__()
        self._transport = httpx.MockTransport(handler)

    def _make_client(self, **overrides: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=True,
            headers=self._headers.copy(),
        )


def form_of(body: str) -> dict[str, str]:
    """The form fields of a recorded urlencoded request body."""
    return dict(httpx.QueryParams(body))


def json_of(body: str) -> dict:
    """The decoded JSON of a recorded request body ({} when it is not JSON)."""
    try:
        return json.loads(body)
    except Exception:
        return {}
