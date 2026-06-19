"""Google OAuth + API client management for AG2 Assistant.

A personal-desktop OAuth flow: the user downloads an OAuth *client* JSON from
Google Cloud to `~/.ag2assistant/google_credentials.json`, runs `ag2assistant google login`
once (browser consent), and the resulting *token* is cached at
`~/.ag2assistant/google_token.json` and silently refreshed thereafter.

The Google client libraries are an optional dependency (`pip install
ag2assistant[google]`); everything here imports them lazily so the rest of AG2 Assistant works
without them.
"""

from pathlib import Path

# Read + send Gmail, read/write Calendar, read-only Drive. (Send is still gated
# by a human-approval prompt at the tool layer.)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
]


def credentials_path() -> Path:
    return Path.home() / ".ag2assistant" / "google_credentials.json"


def token_path() -> Path:
    return Path.home() / ".ag2assistant" / "google_token.json"


def account_path() -> Path:
    return Path.home() / ".ag2assistant" / "google_account.txt"


def account_email() -> str | None:
    """The signed-in account email, cached at login (no network call)."""
    ap = account_path()
    if ap.exists():
        return ap.read_text().strip() or None
    return None


def save_credentials_json(content: str) -> None:
    """Validate and store an uploaded OAuth client JSON to the credentials path."""
    import json

    data = json.loads(content)  # raises if not valid JSON
    if not isinstance(data, dict) or not ({"installed", "web"} & data.keys()):
        raise ValueError(
            "Not an OAuth client file (expected an 'installed' or 'web' key)."
        )
    cp = credentials_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(content)


def is_configured() -> bool:
    """True if the user has placed an OAuth client credentials file."""
    return credentials_path().exists()


def has_token() -> bool:
    """True if a stored (logged-in) token exists."""
    return token_path().exists()


def _require_libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "Google integration needs extra deps. Install with: "
            'pip install "ag2assistant[google]"'
        ) from exc
    return Credentials, Request, InstalledAppFlow


def load_credentials(interactive: bool = False, open_browser: bool = True):
    """Return valid Google credentials, or None if not available.

    Refreshes an expired token silently. If `interactive`, runs the consent
    flow when there's no usable token (and persists the result). `open_browser`
    controls whether the consent page is auto-opened (the gateway flow prints the
    URL instead so a remote client can open it).
    """
    Credentials, Request, InstalledAppFlow = _require_libs()

    creds = None
    tp = token_path()
    if tp.exists():
        creds = Credentials.from_authorized_user_file(str(tp), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception:
            creds = None  # refresh failed → fall through to re-auth

    if not interactive:
        return None

    if not credentials_path().exists():
        raise FileNotFoundError(
            f"Missing OAuth client file at {credentials_path()}. Download a "
            "Desktop OAuth client JSON from Google Cloud and save it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path()), SCOPES
    )
    creds = flow.run_local_server(port=0, open_browser=open_browser)
    _save_token(creds)
    return creds


def _save_token(creds) -> None:
    tp = token_path()
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(creds.to_json())


def _email_for(creds) -> str:
    try:
        from googleapiclient.discovery import build

        profile = (
            build("gmail", "v1", credentials=creds, cache_discovery=False)
            .users()
            .getProfile(userId="me")
            .execute()
        )
        return profile.get("emailAddress", "(unknown)")
    except Exception:
        return "(signed in)"


def _cache_account(email: str) -> None:
    ap = account_path()
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(email)


def login(open_browser: bool = True) -> str:
    """Run the interactive (local-browser) consent flow; returns the account email.

    Used by the CLI. With `open_browser=False` the consent URL is printed instead
    of auto-opened (still completes via the local redirect). The gateway uses the
    URL/callback pair below for its web/channel flow.
    """
    creds = load_credentials(interactive=True, open_browser=open_browser)
    email = _email_for(creds)
    _cache_account(email)
    return email


def make_login_flow(redirect_uri: str):
    """Build an OAuth flow for a redirect-based (gateway) consent.

    Returns (auth_url, state, flow). The caller delivers `auth_url` to the user
    (web button or a channel link), keeps `flow` keyed by `state`, and calls
    `complete_login` when the redirect hits `redirect_uri` with a code.
    """
    Credentials, Request, InstalledAppFlow = _require_libs()
    if not credentials_path().exists():
        raise FileNotFoundError(
            f"Missing OAuth client file at {credentials_path()}."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path()), SCOPES, redirect_uri=redirect_uri
    )
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    return auth_url, state, flow


def complete_login(flow, code: str) -> str:
    """Exchange an authorization `code` for a token; persist it; return the email."""
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_token(creds)
    email = _email_for(creds)
    _cache_account(email)
    return email


def logout() -> bool:
    """Delete the cached token (and account). Returns True if a token was removed."""
    account_path().unlink(missing_ok=True)
    tp = token_path()
    if tp.exists():
        tp.unlink()
        return True
    return False


def build_service(api: str, version: str):
    """Build a Google API client (e.g. ('gmail','v1')), or raise if not logged in."""
    from googleapiclient.discovery import build

    creds = load_credentials(interactive=False)
    if creds is None:
        raise RuntimeError(
            "Not signed in to Google. Run `ag2assistant google login` first."
        )
    return build(api, version, credentials=creds, cache_discovery=False)
