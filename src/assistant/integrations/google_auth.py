"""Google OAuth + API client management for AG2 Assistant.

A personal-desktop OAuth flow: the user downloads an OAuth *client* JSON from
Google Cloud to `~/.ag2assistant/google_credentials.json`, runs `ag2-assistant google login`
once (browser consent), and the resulting *token* is cached at
`~/.ag2assistant/google_token.json` and silently refreshed thereafter.

The Google client libraries are an optional dependency (`pip install
ag2-assistant[google]`); everything here imports them lazily so the rest of AG2 Assistant works
without them.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path

from assistant.paths import Paths

# NOTE: oauthlib treats a scope superset returned by Google as an error ("Scope has
# changed"). OAUTHLIB_RELAX_TOKEN_SCOPE=1 relaxes it and is set at the entry point
# (cli.py) — this module never touches the process environment.

# Least-privilege scopes for exactly what the tools do:
#   gmail.readonly — search + read mail
#   gmail.compose  — create drafts AND send (does NOT allow deleting/relabelling
#                    existing mail, unlike the broader gmail.modify)
#   calendar.events — read + create events only (not calendar/ACL management)
#   drive.readonly  — find + read files
# Sending is additionally gated by a human-approval prompt at the tool layer.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
]


# The remedy differs by how this install was made, and a command that doesn't
# apply is barely better than no command at all: the documented install path is
# `uv tool install` from git (scripts/install.sh), contributors run an editable
# checkout, and only a plain PyPI install wants bare pip. Detect, don't guess.
_GIT_REPO = "https://github.com/ag2ai/ag2-assistant.git"

# Every module the integration needs at runtime: the auth pair (consent + refresh)
# and the API client (every actual Gmail/Calendar/Drive call).
_REQUIRED_MODULES = ("google.auth", "google_auth_oauthlib", "googleapiclient")

# Finding a module is a dependency, so it arrives as a callable: name -> spec|None.
Finder = Callable[[str], object | None]


def default_finder() -> Finder:
    """The real importlib lookup over this interpreter's import path."""
    from importlib.util import find_spec

    return find_spec


def install_hint(module_path: Path, prefix: Path) -> str:
    """The command that actually adds the `[google]` extra to THIS install, decided
    from where this module sits (``module_path``) and the interpreter ``prefix``."""
    if (Path(module_path).resolve().parents[3] / "pyproject.toml").exists():
        return "uv sync --extra google"  # editable checkout (src/assistant/integrations/…)
    if "/uv/tools/" in Path(prefix).as_posix():
        return f'uv tool install --force "ag2-assistant[google] @ git+{_GIT_REPO}@main"'
    return 'pip install "ag2-assistant[google]"'


def libs_available(finder: Finder) -> bool:
    """True if the optional `[google]` client libraries are importable via ``finder``.

    A cached token only proves the user once consented — not that the libraries
    are present. The two come apart routinely: a token written by one venv (or
    an older install that had the extra) is read by another that never installed
    it. Callers gate on `google_ready()` so we never hand the model a tool whose
    first call would be a bare ImportError.
    """
    try:
        return all(finder(m) is not None for m in _REQUIRED_MODULES)
    except (ImportError, ValueError):  # parent package missing, or a broken spec
        return False


def _require_libs():
    try:
        from google.auth.transport.requests import Request  # local: optional [google] extra
        from google.oauth2.credentials import Credentials  # local: optional [google] extra
        from google_auth_oauthlib.flow import InstalledAppFlow  # local: optional [google] extra
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError("Google integration needs the optional [google] extra.") from exc
    return Credentials, Request, InstalledAppFlow


def _email_for(creds) -> str:
    try:
        from googleapiclient.discovery import build  # local: optional [google] extra

        profile = (
            build("gmail", "v1", credentials=creds, cache_discovery=False)
            .users()
            .getProfile(userId="me")
            .execute()
        )
        return profile.get("emailAddress", "(unknown)")
    except Exception:
        return "(signed in)"


class GoogleAuth:
    """Google OAuth + API clients for one install.

    The OAuth client file, the cached token and the remembered account all hang off
    ``paths``; whether the optional client libraries are importable is answered by an
    injected ``finder`` so the check is testable without touching real imports.
    """

    def __init__(
        self,
        paths: Paths,
        *,
        finder: Finder | None = None,
        module_path: Path | None = None,
        prefix: Path | None = None,
    ) -> None:
        self.credentials_path = paths.google_credentials
        self.token_path = paths.google_token
        self.account_path = paths.google_account
        self._finder = finder or default_finder()
        self._module_path = module_path or Path(__file__)
        self._prefix = prefix or Path(sys.prefix)

    def libs_available(self) -> bool:
        """Whether the optional `[google]` client libraries are importable."""
        return libs_available(self._finder)

    def install_hint(self) -> str:
        """The command that adds the `[google]` extra to this install."""
        return install_hint(self._module_path, self._prefix)

    def _missing_libs_message(self) -> str:
        return f"Google integration needs extra deps. Install with: {self.install_hint()}"

    def account_email(self) -> str | None:
        """The signed-in account email, cached at login (no network call)."""
        ap = self.account_path
        if ap.exists():
            return ap.read_text().strip() or None
        return None

    def save_credentials_json(self, content: str) -> None:
        """Validate and store an uploaded OAuth client JSON to the credentials path."""
        data = json.loads(content)  # raises if not valid JSON
        if not isinstance(data, dict) or not ({"installed", "web"} & data.keys()):
            raise ValueError("Not an OAuth client file (expected an 'installed' or 'web' key).")
        cp = self.credentials_path
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(content)
        cp.chmod(0o600)  # contains the OAuth client secret — keep it owner-only

    def is_configured(self) -> bool:
        """True if the user has placed an OAuth client credentials file."""
        return self.credentials_path.exists()

    def has_token(self) -> bool:
        """True if a stored (logged-in) token exists."""
        return self.token_path.exists()

    def google_ready(self) -> bool:
        """True if Google calls can actually be made: signed in *and* libraries present."""
        return self.has_token() and self.libs_available()

    def load_credentials(self, interactive: bool = False, open_browser: bool = True):
        """Return valid Google credentials, or None if not available.

        Refreshes an expired token silently. If `interactive`, runs the consent
        flow when there's no usable token (and persists the result). `open_browser`
        controls whether the consent page is auto-opened (the gateway flow prints the
        URL instead so a remote client can open it).
        """
        Credentials, Request, InstalledAppFlow = _require_libs()

        creds = None
        tp = self.token_path
        if tp.exists():
            creds = Credentials.from_authorized_user_file(str(tp), SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                return creds
            except Exception:
                creds = None  # refresh failed → fall through to re-auth

        if not interactive:
            return None

        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Missing OAuth client file at {self.credentials_path}. Download a "
                "Desktop OAuth client JSON from Google Cloud and save it there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=open_browser)
        self._save_token(creds)
        return creds

    def _save_token(self, creds) -> None:
        tp = self.token_path
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(creds.to_json())
        tp.chmod(0o600)  # holds a long-lived refresh token — keep it owner-only

    def _cache_account(self, email: str) -> None:
        ap = self.account_path
        ap.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text(email)

    def login(self, open_browser: bool = True) -> str:
        """Run the interactive (local-browser) consent flow; returns the account email.

        Used by the CLI. With `open_browser=False` the consent URL is printed instead
        of auto-opened (still completes via the local redirect). The gateway uses the
        URL/callback pair below for its web/channel flow.
        """
        creds = self.load_credentials(interactive=True, open_browser=open_browser)
        email = _email_for(creds)
        self._cache_account(email)
        return email

    def make_login_flow(self, redirect_uri: str):
        """Build an OAuth flow for a redirect-based (gateway) consent.

        Returns (auth_url, state, flow). The caller delivers `auth_url` to the user
        (web button or a channel link), keeps `flow` keyed by `state`, and calls
        `complete_login` when the redirect hits `redirect_uri` with a code.
        """
        Credentials, Request, InstalledAppFlow = _require_libs()
        if not self.credentials_path.exists():
            raise FileNotFoundError(f"Missing OAuth client file at {self.credentials_path}.")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.credentials_path), SCOPES, redirect_uri=redirect_uri
        )
        auth_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )
        return auth_url, state, flow

    def complete_login(self, flow, code: str) -> str:
        """Exchange an authorization `code` for a token; persist it; return the email."""
        flow.fetch_token(code=code)
        creds = flow.credentials
        self._save_token(creds)
        email = _email_for(creds)
        self._cache_account(email)
        return email

    def logout(self) -> bool:
        """Delete the cached token (and account). Returns True if a token was removed."""
        self.account_path.unlink(missing_ok=True)
        tp = self.token_path
        if tp.exists():
            tp.unlink()
            return True
        return False

    def build_service(self, api: str, version: str):
        """Build a Google API client (e.g. ('gmail','v1')), or raise if not usable."""
        try:
            from googleapiclient.discovery import build  # local: optional [google] extra
        except ImportError as exc:
            # Defence in depth: `self.google_ready()` should have kept these tools
            # unregistered, but any path that still lands here gets the remedy
            # rather than a bare "No module named 'googleapiclient'".
            raise ImportError(self._missing_libs_message()) from exc

        creds = self.load_credentials(interactive=False)
        if creds is None:
            raise RuntimeError("Not signed in to Google. Run `ag2-assistant google login` first.")
        return build(api, version, credentials=creds, cache_discovery=False)
