"""Tests for the Google integration wiring (auth state + tool gating).

No real OAuth or API calls — those need the user's credentials and a browser.
These cover credential-state helpers, tool construction, gating of writes, and
conditional inclusion in the agent's tool list.
"""

import io

import pytest
import pytest as _pytest
from fastapi.testclient import TestClient

import assistant.tools as tools_mod
from assistant.agent import GOOGLE_GUIDANCE, turn_prompt
from assistant.config import Config
from assistant.integrations import google_auth
from assistant.integrations.google_auth import GoogleAuth, install_hint, libs_available
from assistant.tools.google import _decode_drive_content, _extract_drive_id, build_google_tools
from tests.support.apps import make_profile_app

# Finders stand in for "are the optional [google] libs importable?" — the real one is
# importlib.find_spec, so a callable answering by name is the whole dependency.
HAS_LIBS = lambda name: object()  # noqa: E731
NO_LIBS = lambda name: None  # noqa: E731


@pytest.fixture
def google(paths) -> GoogleAuth:
    """Google auth over an isolated layout, with the client libraries present."""
    return GoogleAuth(paths, finder=HAS_LIBS)


def _sign_in(paths, email="me@example.com"):
    """A real signed-in state on disk: OAuth client, cached token, remembered email."""
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.google_credentials.write_text('{"installed": {"client_id": "x"}}')
    paths.google_token.write_text("{}")
    paths.google_account.write_text(email)


def test_not_configured_or_signed_in_by_default(google):
    assert google.is_configured() is False
    assert google.has_token() is False


def test_is_configured_when_client_present(google, paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.google_credentials.write_text("{}")
    assert google.is_configured() is True


def test_logout_removes_token(google, paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.google_token.write_text("{}")
    assert google.has_token() is True
    assert google.logout() is True
    assert google.has_token() is False
    assert google.logout() is False  # nothing left to remove


def test_extract_drive_id_from_url_or_id():

    assert (
        _extract_drive_id("https://docs.google.com/spreadsheets/d/1AbC_dEf-123/edit#gid=0")
        == "1AbC_dEf-123"
    )
    assert _extract_drive_id("https://drive.google.com/open?id=XYZ789") == "XYZ789"
    assert _extract_drive_id("rawFileId") == "rawFileId"


def test_google_guidance_in_turn_prompt_when_signed_in(paths, google):
    cfg = Config.for_paths(paths)
    _sign_in(paths)
    assert GOOGLE_GUIDANCE in " ".join(turn_prompt(cfg, google_auth=google))

    # A scoped agent without the Google capabilities is never told to reach for them,
    # even while the user is signed in.
    assert GOOGLE_GUIDANCE not in " ".join(turn_prompt(cfg, google=False, google_auth=google))

    paths.google_token.unlink()  # signed out again
    assert GOOGLE_GUIDANCE not in " ".join(turn_prompt(cfg, google_auth=google))


def test_build_google_tools_names(google):
    names = [t.name for t in build_google_tools(google)]
    assert names == [
        "gmail_search",
        "gmail_read",
        "gmail_send",
        "gmail_create_draft",
        "calendar_list_events",
        "calendar_create_event",
        "drive_search",
        "drive_read",
    ]


def test_drive_read_decodes_text_and_extracts_pdf_but_never_raw_binary():
    """Binary Drive content must never reach the model as mojibake: text mimes
    decode, PDFs get real text extraction, and anything else binary gets an
    honest 'can't read this' message (the regression was a PDF decoded raw,
    poisoning the chat with garbage)."""

    pypdf = pytest.importorskip("pypdf")  # google extra; CI installs it
    PdfWriter = pypdf.PdfWriter

    # text/* and textual application mimes decode as UTF-8
    assert _decode_drive_content("notes.txt", "text/plain", b"hello") == "hello"
    assert _decode_drive_content("d.json", "application/json", b'{"a": 1}') == '{"a": 1}'

    # a real PDF with no text (blank page) → honest message, raw bytes never leak
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    out = _decode_drive_content("bye-laws.pdf", "application/pdf", buf.getvalue())
    assert "%PDF" not in out
    assert "no extractable text" in out

    # a corrupt "PDF" (extraction raises) → same honest path, no crash
    out = _decode_drive_content("broken.pdf", "application/pdf", b"%PDF-1.4 not really")
    assert "no extractable text" in out

    # arbitrary binary → honest message naming the mime, raw bytes never decoded
    out = _decode_drive_content("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0garbage")
    assert "binary file" in out and "image/jpeg" in out
    assert "garbage" not in out


def test_write_tools_are_gated_reads_are_not(google):
    tools = {t.name: t for t in build_google_tools(google)}
    # sends / writes carry the approval middleware; reads/searches don't
    assert tools["gmail_send"]._middleware
    assert tools["calendar_create_event"]._middleware
    assert not tools["gmail_search"]._middleware
    assert not tools["gmail_read"]._middleware
    assert not tools["drive_read"]._middleware
    assert not tools["gmail_create_draft"]._middleware  # draft can't send → ungated


def test_save_credentials_validates(google, paths):
    # valid installed-client JSON is accepted
    google.save_credentials_json('{"installed": {"client_id": "x"}}')
    assert paths.google_credentials.exists()
    # garbage is rejected
    with _pytest.raises(Exception):
        google.save_credentials_json("not json")
    with _pytest.raises(ValueError):
        google.save_credentials_json('{"nope": 1}')


# --- gateway endpoints (mocked auth) ---


def _client(paths, **kwargs):
    """A started app on the isolated layout; only the Google integration is injected."""
    app, _pid = make_profile_app(paths, **kwargs)
    return TestClient(app)


def test_google_status_endpoint(paths):
    _sign_in(paths)
    google = GoogleAuth(paths, finder=HAS_LIBS)
    with _client(paths, google=google) as client:
        st = client.get("/api/google/status").json()
        assert st == {
            "configured": True,
            "signed_in": True,
            "email": "me@example.com",
            "libs_available": True,
            "install_hint": None,
        }


def test_google_status_reports_missing_libs(paths):
    """A token without the [google] extra must not read as a healthy connection —
    the UI needs the remedy, not a green tick."""
    _sign_in(paths)
    google = GoogleAuth(paths, finder=NO_LIBS)
    with _client(paths, google=google) as client:
        st = client.get("/api/google/status").json()
        assert st["signed_in"] is True
        assert st["libs_available"] is False
        assert st["install_hint"] == google.install_hint()
        assert "google" in st["install_hint"]


def test_install_hint_matches_how_this_install_was_made(tmp_path):
    """A remedy for the wrong install method is barely better than none, so the
    hint follows the environment rather than always naming pip. Pure over
    (module location, interpreter prefix) — no environment to patch."""
    from pathlib import Path

    checkout = Path("/repo/src/assistant/integrations/google_auth.py")
    src_tree = tmp_path / "src" / "assistant" / "integrations"
    src_tree.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    # Editable checkout: a pyproject.toml three levels above the module.
    assert install_hint(src_tree / "google_auth.py", Path("/usr/local")) == (
        "uv sync --extra google"
    )

    # Not a checkout → fall through to the install-method checks.
    orphan = tmp_path / "a" / "b" / "c" / "d" / "google_auth.py"
    hint = install_hint(orphan, Path("/home/u/.local/share/uv/tools/ag2-assistant"))
    assert hint.startswith("uv tool install") and "ag2-assistant[google]" in hint

    assert install_hint(orphan, Path("/usr/local")) == 'pip install "ag2-assistant[google]"'
    assert checkout  # documents the shape the real module path has


def test_google_login_url_and_callback(paths):
    """The consent round-trip: the route hands out a URL and, on the redirect back,
    completes the very flow it stored. The OAuth conversation itself is the
    integration's own business, so a GoogleAuth that scripts it is injected."""
    sentinel_flow = object()
    completed = {}

    class ScriptedAuth(GoogleAuth):
        def is_configured(self):
            return True

        def make_login_flow(self, redirect_uri):
            return "https://accounts.google.com/o/oauth2/auth?x=1", "st8", sentinel_flow

        def complete_login(self, flow, code):
            completed.update(flow=flow, code=code)
            return "me@example.com"

    with _client(paths, google=ScriptedAuth(paths, finder=HAS_LIBS)) as client:
        r = client.post("/api/google/login_url").json()
        assert r["ok"] is True
        assert "accounts.google.com" in r["auth_url"]
        # the redirect catches the code and completes the stored flow
        page = client.get("/api/google/callback", params={"state": "st8", "code": "abc"})
        assert page.status_code == 200
        assert "Connected" in page.text
        assert completed["flow"] is sentinel_flow and completed["code"] == "abc"
        # an unknown state is rejected gracefully
        assert (
            "no longer valid"
            in client.get("/api/google/callback", params={"state": "bogus", "code": "x"}).text
        )


def test_google_credentials_upload_endpoint(paths):
    with _client(paths, google=GoogleAuth(paths, finder=HAS_LIBS)) as client:
        ok = client.post(
            "/api/google/credentials", json={"content": '{"installed": {"client_id": "x"}}'}
        ).json()
        assert ok["ok"] is True
        assert paths.google_credentials.exists()
        bad = client.post("/api/google/credentials", json={"content": "nope"}).json()
        assert bad["ok"] is False


def test_agent_tools_include_google_only_when_signed_in(paths, google):
    cfg = Config.for_paths(paths)
    base = [t.name for t in tools_mod.build_agent_tools(config=cfg, google=google)]
    assert not any(n.startswith("gmail_") for n in base)  # no token yet

    _sign_in(paths)
    signed_in = [t.name for t in tools_mod.build_agent_tools(config=cfg, google=google)]
    assert "gmail_send" in signed_in
    assert "calendar_list_events" in signed_in
    assert "drive_search" in signed_in


def test_google_tools_withheld_when_libs_missing(paths):
    """Signed in but no [google] extra: the model must not be handed tools whose
    every call would raise ImportError — that's the dead end this guards."""
    cfg = Config.for_paths(paths)
    _sign_in(paths)
    no_libs = GoogleAuth(paths, finder=NO_LIBS)

    names = [t.name for t in tools_mod.build_agent_tools(config=cfg, google=no_libs)]
    assert not any(n.startswith(("gmail_", "calendar_", "drive_")) for n in names)

    caps = tools_mod.available_capabilities(cfg, google=no_libs)
    assert "gmail" not in caps and "calendar" not in caps and "drive" not in caps

    # ...and the model isn't told to reach for them either.
    assert GOOGLE_GUIDANCE not in " ".join(turn_prompt(cfg, google_auth=no_libs))


def test_google_ready_needs_both_token_and_libs(paths):
    for token, libs, expected in [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ]:
        paths.root.mkdir(parents=True, exist_ok=True)
        if token:
            paths.google_token.write_text("{}")
        elif paths.google_token.exists():
            paths.google_token.unlink()
        auth = GoogleAuth(paths, finder=HAS_LIBS if libs else NO_LIBS)
        assert auth.google_ready() is expected


def test_build_service_gives_install_hint_not_import_error(google):
    """Defence in depth: any path reaching build_service without the libs gets
    the remedy, never a bare "No module named 'googleapiclient'"."""
    if libs_available(google_auth.default_finder()):
        pytest.skip("the [google] extra is installed, so the import cannot fail here")

    with _pytest.raises(ImportError) as exc:
        google.build_service("gmail", "v1")
    msg = str(exc.value)
    assert msg == google._missing_libs_message()
    assert "Install with:" in msg and google.install_hint() in msg
    assert msg != "No module named 'googleapiclient'"  # never the bare error
