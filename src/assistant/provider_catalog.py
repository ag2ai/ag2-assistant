"""Ask a provider endpoint which models it offers, for the Model field's combobox.

Answers in ``coding/model_catalog.py``'s ``{models, current, reason}`` envelope, or
names which of the four reasons stopped it.
"""

import httpx

# Why a catalog could not be read: a rejected credential, an endpoint that could not
# be reached, one that answered but lists nothing, and a type no probe applies to.
UNAUTHORIZED = "unauthorized"
UNREACHABLE = "unreachable"
NO_LIST_ENDPOINT = "no_list_endpoint"
NOT_PROBEABLE = "not_probeable"

# The types the gateway probes, and the one it answers without probing.
GATEWAY_PROBEABLE = ("ollama", "gemini", "openai", "openai_responses", "anthropic")
NEVER_PROBEABLE = ("openai_subscription",)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_BASE_URL = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openai": "https://api.openai.com/v1",
    "openai_responses": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}
ANTHROPIC_VERSION = "2023-06-01"
# Default seconds for one HTTP read against a provider; callers pass their own.
DEFAULT_PROBE_TIMEOUT = 8.0


class CatalogUnavailable(Exception):
    """No catalog could be read, and ``reason`` is one of the four tokens above."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CatalogTarget:
    """A resolved place to ask for a model list, with the key already looked up."""

    __slots__ = ("type", "base_url", "host", "api_key")

    def __init__(self, type: str, base_url: str = "", host: str = "", api_key: str = "") -> None:
        self.type = type
        self.base_url = base_url
        self.host = host
        self.api_key = api_key

    def __repr__(self) -> str:  # the key is deliberately not in it
        return f"CatalogTarget(type={self.type!r}, base_url={self.base_url!r}, host={self.host!r})"


def _ollama_tags(payload: dict) -> list[str]:
    """The tags pulled on that host; ``no_list_endpoint`` when the payload has no
    ``models`` array."""
    models = payload.get("models")
    if not isinstance(models, list):
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    return [str(m.get("name") or m.get("model") or "") for m in models if isinstance(m, dict)]


def _gemini_models(payload: dict) -> list[str]:
    """Gemini's models, keeping every entry that can ``generateContent`` and every
    entry that declares no generation methods at all."""
    models = payload.get("models")
    if not isinstance(models, list):
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    out = []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        methods = entry.get("supportedGenerationMethods")
        if isinstance(methods, list) and methods and "generateContent" not in methods:
            continue
        out.append(str(entry.get("name") or "").removeprefix("models/"))
    return out


def _data_ids(payload: dict) -> list[str]:
    """The ids in the OpenAI/Anthropic ``{"data": [{"id": ...}]}`` shape. Non-chat
    names are dropped client-side, by one deny-list shared with the browser probe."""
    data = payload.get("data")
    if not isinstance(data, list):
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    return [str(e.get("id") or "") for e in data if isinstance(e, dict)]


def _request(target: CatalogTarget) -> tuple[str, dict[str, str]]:
    """Where to ask ``target`` for its models, and with what headers. A custom
    endpoint is asked at its own address, not at its vendor's."""
    ctype = target.type
    if ctype == "ollama":
        return (target.host or DEFAULT_OLLAMA_HOST).rstrip("/") + "/api/tags", {}
    base = (target.base_url or DEFAULT_BASE_URL[ctype]).rstrip("/")
    if ctype == "gemini":
        # Header, not the `?key=` the browser path must use: a URL reaches our logs.
        return f"{base}/models", {"x-goog-api-key": target.api_key}
    if ctype == "anthropic":
        return f"{base}/models", {
            "x-api-key": target.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    return f"{base}/models", {"Authorization": f"Bearer {target.api_key}"}


def status_reason(status: int) -> str:
    """What an HTTP status says about why no catalog came back; "" when it says
    nothing went wrong. A 5xx or a 429 is a bad moment, not a missing list."""
    if status in (401, 403):
        return UNAUTHORIZED
    if status == 429 or status >= 500:
        return UNREACHABLE
    if status >= 400:
        return NO_LIST_ENDPOINT
    return ""


async def probe_provider_models(
    target: CatalogTarget,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> list[str]:
    """The model ids ``target`` offers, or raise :class:`CatalogUnavailable`.

    The production probe behind the app's ``llm_catalog_probe`` seam; ``client`` lets
    a test drive it over a MockTransport.
    """
    if target.type not in GATEWAY_PROBEABLE:
        raise CatalogUnavailable(NOT_PROBEABLE)
    # Nothing to ask with, and nothing was asked — never a failure to reach anything.
    if target.type != "ollama" and not target.api_key and not target.base_url:
        raise CatalogUnavailable(NOT_PROBEABLE)
    url, headers = _request(target)
    owned = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        response = await http.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise CatalogUnavailable(UNREACHABLE) from exc
    finally:
        if owned:
            await http.aclose()
    reason = status_reason(response.status_code)
    if reason:
        raise CatalogUnavailable(reason)
    try:
        payload = response.json()
    except ValueError as exc:
        raise CatalogUnavailable(NO_LIST_ENDPOINT) from exc
    if not isinstance(payload, dict):
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    if target.type == "ollama":
        names = _ollama_tags(payload)
    elif target.type == "gemini":
        names = _gemini_models(payload)
    else:
        names = _data_ids(payload)
    return [name for name in names if name]
