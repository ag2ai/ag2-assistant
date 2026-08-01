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
GATEWAY_PROBEABLE = ("ollama", "gemini", "openai", "openai_responses", "anthropic")
# ChatGPT-subscription models have no API key to probe with, permanently.
NEVER_PROBEABLE = ("openai_subscription",)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_BASE_URL = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openai": "https://api.openai.com/v1",
    "openai_responses": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}
ANTHROPIC_VERSION = "2023-06-01"
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


def _gemini_models(payload: dict) -> list[str]:
    """Gemini's own metadata decides what is a chat model: an entry that declares
    generation methods keeps only if it can ``generateContent``. One that declares
    none is kept — an unrecognised shape is offered, never hidden."""
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
    """The OpenAI/Anthropic list shape: ``{"data": [{"id": ...}]}``. Both return
    chat models among others; what is not a chat model is filtered client-side, by
    one deny-list shared with the browser probe."""
    data = payload.get("data")
    if not isinstance(data, list):
        raise CatalogUnavailable(NO_LIST_ENDPOINT)
    return [str(e.get("id") or "") for e in data if isinstance(e, dict)]


def _request(target: CatalogTarget) -> tuple[str, dict[str, str]]:
    """Where to ask ``target`` for its models, and with what headers. A custom
    endpoint is asked at its own address, not at the vendor whose wire it speaks."""
    ctype = target.type
    if ctype == "ollama":
        return (target.host or DEFAULT_OLLAMA_HOST).rstrip("/") + "/api/tags", {}
    base = (target.base_url or DEFAULT_BASE_URL[ctype]).rstrip("/")
    if ctype == "gemini":
        return f"{base}/models?key={target.api_key}", {}
    if ctype == "anthropic":
        return f"{base}/models", {
            "x-api-key": target.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    return f"{base}/models", {"Authorization": f"Bearer {target.api_key}"}


async def probe_provider_models(
    target: CatalogTarget, *, client: httpx.AsyncClient | None = None
) -> list[str]:
    """The model ids ``target`` offers, or raise :class:`CatalogUnavailable`.

    The production probe behind the app's ``llm_catalog_probe`` seam. ``client`` is
    injected by tests that want the read to go over a MockTransport.
    """
    if target.type not in GATEWAY_PROBEABLE:
        raise CatalogUnavailable(NOT_PROBEABLE)
    # A keyed type with neither a key nor an endpoint of its own has nothing to ask
    # with. That is not a failure to reach anything, so it never reads as unreachable.
    if target.type != "ollama" and not target.api_key and not target.base_url:
        raise CatalogUnavailable(NOT_PROBEABLE)
    url, headers = _request(target)
    owned = client is None
    http = client or httpx.AsyncClient(timeout=PROBE_TIMEOUT)
    try:
        response = await http.get(url, headers=headers)
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
    if target.type == "ollama":
        names = _ollama_tags(payload)
    elif target.type == "gemini":
        names = _gemini_models(payload)
    else:
        names = _data_ids(payload)
    return [name for name in names if name]
