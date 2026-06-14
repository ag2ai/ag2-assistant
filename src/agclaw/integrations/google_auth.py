"""Google OAuth + API client management for AGClaw.

A personal-desktop OAuth flow: the user downloads an OAuth *client* JSON from
Google Cloud to `~/.agclaw/google_credentials.json`, runs `agclaw google login`
once (browser consent), and the resulting *token* is cached at
`~/.agclaw/google_token.json` and silently refreshed thereafter.

The Google client libraries are an optional dependency (`pip install
agclaw[google]`); everything here imports them lazily so the rest of AGClaw works
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
    return Path.home() / ".agclaw" / "google_credentials.json"


def token_path() -> Path:
    return Path.home() / ".agclaw" / "google_token.json"


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
            'pip install "agclaw[google]"'
        ) from exc
    return Credentials, Request, InstalledAppFlow


def load_credentials(interactive: bool = False):
    """Return valid Google credentials, or None if not available.

    Refreshes an expired token silently. If `interactive`, runs the browser
    consent flow when there's no usable token (and persists the result).
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
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _save_token(creds) -> None:
    tp = token_path()
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(creds.to_json())


def login() -> str:
    """Run the interactive consent flow; returns the authorised account email."""
    creds = load_credentials(interactive=True)
    try:
        from googleapiclient.discovery import build

        profile = (
            build("gmail", "v1", credentials=creds)
            .users()
            .getProfile(userId="me")
            .execute()
        )
        return profile.get("emailAddress", "(unknown)")
    except Exception:
        return "(signed in)"


def logout() -> bool:
    """Delete the cached token. Returns True if one was removed."""
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
            "Not signed in to Google. Run `agclaw google login` first."
        )
    return build(api, version, credentials=creds, cache_discovery=False)
