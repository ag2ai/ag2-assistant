"""Browser-facing guards shared by the gateway and the standalone HITL server."""

import base64
import hashlib
import re
from collections.abc import Set as AbstractSet
from urllib.parse import urlsplit

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# A server-rendered page with no scripts: inline styles only.
STATIC_PAGE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

_INLINE_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL)


def origin_ok(
    origin: str | None, host: str | None, allowed: AbstractSet[str] = frozenset()
) -> bool:
    """Whether a browser request may proceed: no Origin, same host:port, or allowlisted.
    Browsers always send `Origin` cross-origin, so anything else is another site."""
    if not origin:
        return True
    origin = origin.rstrip("/")
    if origin in allowed:
        return True
    return bool(host) and urlsplit(origin).netloc == host


def guarded(method: str, path: str) -> bool:
    """Whether a request needs the origin check: any `/api/` call or any state-changing one."""
    return path.startswith("/api/") or method.upper() not in SAFE_METHODS


def spa_csp(index_html: str) -> str:
    """The SPA's CSP: own scripts, the three.js CDN, blob worklets, and its inline importmap."""
    hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
        for body in _INLINE_SCRIPT.findall(index_html)
        if body.strip()
    )
    return (
        f"script-src 'self' {hashes} https://cdn.jsdelivr.net blob:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    )
