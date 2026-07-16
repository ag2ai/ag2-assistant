"""OpenAI Codex / ChatGPT *subscription* auth ("Sign in with ChatGPT").

Lets the assistant run on a user's ChatGPT Plus/Pro subscription instead of a
pay-per-token ``OPENAI_API_KEY``. This is the same mechanism the Codex CLI,
OpenClaw, and Hermes use: OAuth 2.0 + PKCE against OpenAI, then model requests
are routed through the ChatGPT backend (``chatgpt.com/backend-api/codex``) with
the OAuth access token as a Bearer credential + a ``chatgpt-account-id`` header.

⚠️  This path is UNOFFICIAL / reverse-engineered and likely violates OpenAI's
Terms of Service (the subscription is meant for OpenAI's own products). An
account could be rate-limited or restricted. The OAuth constants and backend
endpoint change without notice, so every one is overridable via an env var
(``AG2ASSISTANT_CODEX_*``) to allow fixing a drift without a code change.

Tokens live in ``<data_dir>/codex_auth.json`` (0600), SEPARATE from
``secrets.json`` (that store models "one provider = one key string"; this is a
multi-field, auto-refreshed credential). Network calls are synchronous
(``httpx``); async callers (the gateway) wrap them in ``asyncio.to_thread`` —
mirroring ``integrations.google_auth``.
"""

import base64
import contextlib
import hashlib
import json
import os
import secrets as _secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx

from assistant.config import data_dir

# --- Reverse-engineered constants (all env-overridable; VERIFY against a live
#     Codex CLI if OpenAI changes them) ------------------------------------- #


def _const(env: str, default: str) -> str:
    return os.environ.get(env, default)


CLIENT_ID = _const("AG2ASSISTANT_CODEX_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
AUTH_URL = _const("AG2ASSISTANT_CODEX_AUTH_URL", "https://auth.openai.com/oauth/authorize")
TOKEN_URL = _const("AG2ASSISTANT_CODEX_TOKEN_URL", "https://auth.openai.com/oauth/token")
# The ChatGPT backend the model requests go to. The OpenAI SDK appends "/responses".
BACKEND_BASE = _const("AG2ASSISTANT_CODEX_BASE_URL", "https://chatgpt.com/backend-api/codex")
CALLBACK_PORT = int(_const("AG2ASSISTANT_CODEX_CALLBACK_PORT", "1455"))
REDIRECT_URI = _const(
    "AG2ASSISTANT_CODEX_REDIRECT_URI", f"http://localhost:{CALLBACK_PORT}/auth/callback"
)
SCOPES = "openid profile email offline_access"
ORIGINATOR = _const("AG2ASSISTANT_CODEX_ORIGINATOR", "codex_cli_rs")
OPENAI_BETA = _const("AG2ASSISTANT_CODEX_OPENAI_BETA", "responses=experimental")
# The backend gates its model catalog on the client's version header: without one
# (or with an old one) new model families 404 as "Model not found" even though the
# account has them — observed live when gpt-5.6-* launched while gpt-5.5 kept
# working. Pin a recent Codex CLI version; bump via env when a new family drops.
CLIENT_VERSION = _const("AG2ASSISTANT_CODEX_CLIENT_VERSION", "0.144.1")

# Refresh this many seconds BEFORE the token actually expires (clock-skew margin).
_REFRESH_MARGIN_S = 60


class CodexAuthError(Exception):
    """Not signed in, or a token exchange/refresh failed. Carries a human message
    the CLI/UI surfaces verbatim (e.g. "sign in again")."""


@dataclass
class Creds:
    """The minimum a model request needs: a live access token + account id."""

    access_token: str
    account_id: str | None


# --- Token store ------------------------------------------------------------ #


def _path():
    return data_dir() / "codex_auth.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text())
    except Exception:
        return {}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    try:
        p.chmod(0o600)
    except Exception:
        pass


def _codex_cli_path() -> Path:
    """The official Codex CLI's credential file. Resolved lazily (honors the current
    HOME / the env override) — NOT a module constant, so tests that isolate HOME
    don't read the developer's real ~/.codex/auth.json. Env: AG2ASSISTANT_CODEX_CLI_AUTH."""
    return Path(
        os.environ.get("AG2ASSISTANT_CODEX_CLI_AUTH") or Path.home() / ".codex" / "auth.json"
    )


def _read_codex_cli() -> dict:
    """The official Codex CLI's ``auth.json`` tokens sub-map (empty if absent/no
    subscription session — e.g. the CLI is signed in with an API key instead). Shape:
    {"tokens": {"id_token","access_token","refresh_token","account_id"}, "auth_mode", …}."""
    try:
        data = json.loads(_codex_cli_path().read_text())
    except Exception:
        return {}
    toks = data.get("tokens")
    return toks if isinstance(toks, dict) and toks.get("access_token") else {}


def _codex_cli_creds() -> "Creds | None":
    """Live credentials from the Codex CLI's own session, or None. Read-only: we do
    NOT refresh (that would rotate the CLI's refresh token and log it out). The CLI
    keeps its own token fresh; if it's expired the model call fails cleanly and the
    user re-runs `codex login`."""
    toks = _read_codex_cli()
    access = toks.get("access_token")
    if not access:
        return None
    account = toks.get("account_id") or account_id_from(toks.get("id_token", ""), access)
    return Creds(access_token=access, account_id=account)


# --- PKCE + JWT helpers ----------------------------------------------------- #


def _b64url(raw: bytes) -> str:
    """Base64url without padding (RFC 7636 / JWT convention)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for PKCE S256."""
    verifier = _b64url(_secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _decode_jwt_payload(token: str) -> dict:
    """Decode a JWT's payload WITHOUT verifying the signature (we only read claims
    from a token OpenAI just issued to us over TLS — not a trust decision)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore padding
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def account_id_from(id_token: str, access_token: str) -> str | None:
    """Extract the ChatGPT account id, trying id_token then access_token, with the
    three-level claim fallback the Codex flow uses."""
    for token in (id_token, access_token):
        if not token:
            continue
        claims = _decode_jwt_payload(token)
        if not claims:
            continue
        if acc := claims.get("chatgpt_account_id"):
            return acc
        auth = claims.get("https://api.openai.com/auth")
        if isinstance(auth, dict):
            if acc := auth.get("chatgpt_account_id"):
                return acc
            orgs = auth.get("organizations")
            if isinstance(orgs, list) and orgs and isinstance(orgs[0], dict):
                if acc := orgs[0].get("id"):
                    return acc
    return None


# --- OAuth flow ------------------------------------------------------------- #


def build_authorize_url(challenge: str, state: str) -> str:
    """The OpenAI consent URL the user opens to sign in with ChatGPT."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # Codex-flow specifics (put org/account claims into the id_token).
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": ORIGINATOR,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _store_tokens(tokens: dict) -> Creds:
    """Persist a token response, computing an absolute expiry + account id."""
    access = tokens.get("access_token", "")
    refresh = tokens.get("refresh_token", "")
    id_token = tokens.get("id_token", "")
    expires_in = int(tokens.get("expires_in", 3600) or 3600)
    account_id = account_id_from(id_token, access)
    data = _read()
    data.update(
        {
            "access_token": access,
            # OpenAI rotates refresh tokens — keep the previous one if none returned.
            "refresh_token": refresh or data.get("refresh_token", ""),
            "id_token": id_token or data.get("id_token", ""),
            "expires_at": time.time() + expires_in,
            "account_id": account_id or data.get("account_id"),
        }
    )
    _write(data)
    return Creds(access_token=access, account_id=data["account_id"])


def extract_auth_code(raw: str) -> str:
    """Normalize a pasted value into the bare OAuth ``code``.

    The headless/Docker fallback asks the user to copy the value out of the browser
    after OpenAI redirects. Depending on where they copy from, that value is either
    the bare code OR the whole redirect URL — including the "this site can't be
    reached" page, whose address bar still holds
    ``http://localhost:1455/auth/callback?code=...&state=...``. Accept both: if it
    looks like a URL (or a bare ``code=...&...`` query), pull out the ``code`` param;
    otherwise return it stripped as-is."""
    import urllib.parse

    raw = (raw or "").strip()
    if "code=" in raw:
        query = urllib.parse.urlsplit(raw).query or raw
        code = (urllib.parse.parse_qs(query).get("code") or [""])[0]
        if code:
            return code
    return raw


def exchange_code(code: str, verifier: str) -> Creds:
    """Exchange an authorization code for tokens and store them."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            timeout=30.0,
        )
    except Exception as exc:
        raise CodexAuthError(f"Token exchange request failed: {exc}") from exc
    if resp.status_code != 200:
        raise CodexAuthError(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")
    return _store_tokens(resp.json())


def _refresh(refresh_token: str) -> Creds:
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "scope": SCOPES,
            },
            timeout=30.0,
        )
    except Exception as exc:
        raise CodexAuthError(f"Token refresh request failed: {exc}") from exc
    if resp.status_code != 200:
        raise CodexAuthError(f"Token refresh failed ({resp.status_code}). Please sign in again.")
    return _store_tokens(resp.json())


# --- Public API used by the agent / gateway / CLI --------------------------- #


def is_signed_in() -> bool:
    """Signed in via our own OAuth OR the official Codex CLI's existing session."""
    return bool(_read().get("refresh_token")) or bool(_read_codex_cli())


def ensure_fresh() -> Creds:
    """Return live credentials, refreshing the access token if it is expired (or
    within the skew margin). If we have no session of our own, fall back to the
    Codex CLI's existing session (read-only). Raises ``CodexAuthError`` if neither."""
    data = _read()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        # No AG2-owned login — reuse the Codex CLI's session if it's signed in.
        cli = _codex_cli_creds()
        if cli:
            return cli
        raise CodexAuthError(
            "Not signed in with ChatGPT. Run `ag2-assistant auth login`, "
            "or sign in with the Codex CLI (`codex login`)."
        )
    expires_at = float(data.get("expires_at", 0) or 0)
    access = data.get("access_token", "")
    if access and time.time() < expires_at - _REFRESH_MARGIN_S:
        return Creds(access_token=access, account_id=data.get("account_id"))
    return _refresh(refresh_token)


def creds_best_effort() -> Creds:
    """Return whatever credentials we have WITHOUT ever raising — try a fresh token,
    but on any failure (not signed in, refresh blocked by a geo/network error) fall
    back to the stored (possibly stale/empty) access token.

    Used at agent-BUILD time so constructing the model client never throws: a bad
    token then surfaces as a clean failed turn (the real OpenAI error) instead of a
    500 that takes down reload() and every route that rebuilds the agent."""
    try:
        return ensure_fresh()
    except CodexAuthError:
        data = _read()
        return Creds(access_token=data.get("access_token", ""), account_id=data.get("account_id"))


def default_headers(creds: Creds, session_id: str | None = None) -> dict[str, str]:
    """The extra HTTP headers the ChatGPT backend requires (beyond the SDK's own
    ``Authorization: Bearer <access_token>``, which comes from api_key)."""
    headers = {"OpenAI-Beta": OPENAI_BETA, "originator": ORIGINATOR, "version": CLIENT_VERSION}
    if creds.account_id:
        headers["chatgpt-account-id"] = creds.account_id
    if session_id:
        headers["session_id"] = session_id
    return headers


def status() -> dict:
    """Presence + a non-sensitive hint for the UI. Never returns raw tokens.
    ``source`` is "ag2" (our own OAuth) or "codex-cli" (reusing the CLI's session)."""
    data = _read()
    if data.get("refresh_token"):
        return {
            "signed_in": True,
            "source": "ag2",
            "account_id": data.get("account_id"),
            "expires_at": data.get("expires_at"),
        }
    cli = _codex_cli_creds()
    if cli:
        return {
            "signed_in": True,
            "source": "codex-cli",
            "account_id": cli.account_id,
            "expires_at": None,
        }
    return {"signed_in": False, "source": None, "account_id": None, "expires_at": None}


def logout() -> bool:
    """Remove stored tokens. Returns True if a session existed."""
    p = _path()
    existed = p.exists()
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    return existed


# --- Interactive login (CLI / local) ---------------------------------------- #

_DONE_PAGE = (
    "<!doctype html><meta charset=utf-8><title>{title}</title>"
    "<body style='font-family:system-ui;max-width:520px;margin:14vh auto;"
    "text-align:center;color:#171717'>"
    "<h1 style='color:#f95339'>{title}</h1><p>{msg}</p>"
    "<p style='color:#737373'>You can close this tab and return to the terminal.</p></body>"
)


def _bind_callback_server(handler):
    """Bind the loopback callback server, catching BOTH IPv4 (127.0.0.1) and IPv6
    (::1). This matters because ``localhost`` resolves to ``::1`` first on macOS —
    an IPv4-only listener there yields "connection refused" on the redirect. Prefer a
    dual-stack IPv6 socket (V6ONLY off → also accepts IPv4-mapped); fall back to IPv4."""
    import http.server
    import socket

    class _DualStack(http.server.HTTPServer):
        address_family = socket.AF_INET6
        allow_reuse_address = True

        def server_bind(self):
            with contextlib.suppress(OSError):
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    try:
        return _DualStack(("", CALLBACK_PORT), handler)
    except OSError:
        return http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), handler)


def _capture_code(state: str, timeout_s: float = 300.0) -> str:
    """Run a loopback HTTP server on the callback port and return the auth ``code``
    once OpenAI redirects back. Keeps serving (ignoring stray hits — favicon probes,
    IPv4/IPv6 retries) until the real ``/auth/callback`` arrives or the deadline
    passes. Raises CodexAuthError on timeout / OAuth error / state mismatch."""
    import contextlib as _contextlib
    import http.server
    import time as _time
    import urllib.parse

    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):  # silence default stderr logging
            pass

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if not parsed.path.startswith("/auth/callback"):
                # a stray request (favicon, probe) — answer and keep listening
                self.send_response(204)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(parsed.query)
            captured["code"] = (q.get("code") or [""])[0]
            captured["state"] = (q.get("state") or [""])[0]
            captured["error"] = (q.get("error") or [""])[0]
            ok = bool(captured["code"]) and captured["state"] == state and not captured["error"]
            title = "Signed in ✓" if ok else "Sign-in failed"
            msg = (
                "AG2 Assistant is now connected to your ChatGPT subscription."
                if ok
                else f"{captured['error'] or 'Invalid response'}"
            )
            body = _DONE_PAGE.format(title=title, msg=msg).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = _bind_callback_server(Handler)
    except OSError as exc:
        raise CodexAuthError(
            f"Cannot bind callback port {CALLBACK_PORT} ({exc}). "
            "Use the manual (paste-the-code) flow instead."
        ) from exc

    server.timeout = 1.0  # poll interval; loop below enforces the real deadline
    deadline = _time.monotonic() + timeout_s
    with server:
        # Loop, not a single handle_request(): a stray hit (favicon / a browser's
        # IPv6-then-IPv4 retry) must not consume our one shot and close the socket.
        while not captured.get("code") and not captured.get("error"):
            if _time.monotonic() >= deadline:
                break
            with _contextlib.suppress(Exception):
                server.handle_request()  # returns after `timeout` if no request
    if captured.get("error"):
        raise CodexAuthError(f"OpenAI returned: {captured['error']}")
    if not captured.get("code"):
        raise CodexAuthError("Timed out waiting for the sign-in callback.")
    if captured.get("state") != state:
        raise CodexAuthError("State mismatch on callback (possible CSRF); aborted.")
    return captured["code"]


def run_local_login(open_browser: bool = True) -> Creds:
    """Full local login: generate PKCE, open the consent page, capture the code on
    the loopback port, exchange it, and store tokens. Returns live creds.

    For headless setups where a browser/loopback isn't available, print the URL
    (``open_browser=False``) and use ``exchange_code`` with a pasted code instead.
    """
    verifier, challenge = generate_pkce()
    state = _secrets.token_urlsafe(24)
    url = build_authorize_url(challenge, state)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    code = _capture_code(state)
    return exchange_code(code, verifier)
