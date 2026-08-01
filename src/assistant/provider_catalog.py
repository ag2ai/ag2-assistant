"""Ask a provider endpoint which models it offers, for the Model field's combobox.

The sibling of ``coding/model_catalog.py``: that one asks an ACP adapter behind a
CLI login, this one asks a provider endpoint behind a keyed or keyless Text model.
Both answer in the same ``{models, current, reason}`` envelope.

A catalog is the sole authority on which model names exist. When one cannot be
read, the reason is named rather than hidden — the Model field says which of the
four cases applies and offers Known models in the meantime.
"""

import httpx

# Why a catalog could not be read. `unauthorized` is the credential being rejected,
# `unreachable` a dead endpoint (or, on the browser path, a CORS refusal a browser
# cannot tell apart from one), `no_list_endpoint` a live endpoint that publishes no
# list, and `not_probeable` a type no probe can work on in principle.
UNAUTHORIZED = "unauthorized"
UNREACHABLE = "unreachable"
NO_LIST_ENDPOINT = "no_list_endpoint"
NOT_PROBEABLE = "not_probeable"

# The types the gateway can probe. A keyed type's key comes from its Secret, never
# from the request — a pasted key is the browser's to send (ADR 0024).
GATEWAY_PROBEABLE = ("ollama",)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
# One HTTP read against a provider. Short: the field stays typeable while it runs,
# and a slow provider must not hold the request open.
PROBE_TIMEOUT = 8.0


class CatalogUnavailable(Exception):
    """No catalog could be read, and ``reason`` is one of the four tokens above."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CatalogTarget:
    """A resolved place to ask for a model list — never the configuration itself.

    ``api_key`` is resolved from the entry's Secret by the caller; the route that
    builds one of these accepts no key material as input.
    """

    __slots__ = ("type", "base_url", "host", "api_key")

    def __init__(self, type: str, base_url: str = "", host: str = "", api_key: str = "") -> None:
        self.type = type
        self.base_url = base_url
        self.host = host
        self.api_key = api_key

    def __repr__(self) -> str:  # the key is deliberately not in it
        return f"CatalogTarget(type={self.type!r}, base_url={self.base_url!r}, host={self.host!r})"


def _ollama_tags(payload: dict) -> list[str]:
    """The tags actually pulled on that host. A response without a ``models`` array
    came from something that is not Ollama, so it publishes no list we can read."""
    models = payload.get("models")
    if not isinstance(models, list):
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    return [str(m.get("name") or m.get("model") or "") for m in models if isinstance(m, dict)]


async def probe_provider_models(
    target: CatalogTarget, *, client: httpx.AsyncClient | None = None
) -> list[str]:
    """The model ids ``target`` offers, or raise :class:`CatalogUnavailable`.

    The production probe behind the app's ``llm_catalog_probe`` seam. ``client`` is
    injected by tests that want the read to go over a MockTransport.
    """
    if target.type != "ollama":
        raise CatalogUnavailable(NOT_PROBEABLE)
    url = (target.host or DEFAULT_OLLAMA_HOST).rstrip("/") + "/api/tags"
    owned = client is None
    http = client or httpx.AsyncClient(timeout=PROBE_TIMEOUT)
    try:
        response = await http.get(url)
    except httpx.HTTPError as exc:
        raise CatalogUnavailable(UNREACHABLE) from exc
    finally:
        if owned:
            await http.aclose()
    if response.status_code in (401, 403):
        raise CatalogUnavailable(UNAUTHORIZED)
    if response.status_code >= 400:
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    try:
        payload = response.json()
    except ValueError as exc:
        raise CatalogUnavailable(NO_LIST_ENDPOINT) from exc
    if not isinstance(payload, dict):
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    return [tag for tag in _ollama_tags(payload) if tag]
