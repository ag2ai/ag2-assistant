"""Phase-6 routes answer bodies their response models accept.

The 20 routes here — Skills in both scopes and the install-wide permission store
— run in the two states that stress a model. Empty first, because that is where a
field wrongly declared required turns a green route into a 500: a fresh install
has no granted command, no Global skill and no profile-owned skill, and a search
or a discover over a source holding nothing answers an empty list rather than a
different shape. Then populated, because the model is the contract, so a key it
forgot to declare vanishes from the wire silently rather than failing loudly.

The pair that gets particular attention is the two skill projections. They are
one domain seen from two scopes and differ by exactly two fields, which is the
easy mistake to make in both directions: an install-wide row must NOT carry
``suppressed``/``available`` (nothing install-wide resolved them), and a profile
row must carry both on every row, including the profile's own skills, which
reach the projection down a second code path.
"""

import io
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.apps import api, make_profile_app
from tests.support.http import ScriptedSkillsClient
from tests.support.stubs import skill_tarball

SKILL_KEYS = {"name", "description", "origin", "enabled"}
PROFILE_SKILL_KEYS = SKILL_KEYS | {"suppressed", "available"}
DISCOVERED_KEYS = {"name", "description"}
SEARCH_HIT_KEYS = {"name", "install_id", "description", "installs"}

_SEARCH_HITS = [
    {
        "name": "standalone",
        "skillId": "",
        "source": "me/standalone",
        "description": "A standalone skill",
        "installs": 7,
    }
]


def _registry(hits=_SEARCH_HITS, *, skill="standalone", desc="installed via registry"):
    """A scripted skills.sh registry: ``/search`` answers ``hits``, the tarball
    endpoint answers a real archive carrying ``skill``. ``hits=[]`` is the empty
    state — a query nothing matches."""

    def handle(request: httpx.Request) -> httpx.Response:
        if "/search" in request.url.path:
            return httpx.Response(200, json={"skills": hits})
        return httpx.Response(
            200,
            content=skill_tarball(skill, description=desc),
            headers={"content-type": "application/gzip"},
        )

    return ScriptedSkillsClient(handle)


@pytest.fixture
def app_client(paths):
    """A started single-profile app over a scripted registry: the Global layer holds
    no user-installed skill and the profile owns none until a test says otherwise."""
    app, pid = make_profile_app(paths, persist=True, skills_client=_registry())
    with TestClient(app) as client:
        yield client, pid


def _skill_md(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\nBody of {name}.\n"


def _git_repo(root: Path, skills: dict[str, str]) -> str:
    """A real local git repo with one dir per skill under skills/ — ``git clone
    --depth 1`` takes a local path as the "URL", so discover/install run for real.
    An empty ``skills`` mapping is the source that holds nothing."""
    root.mkdir(parents=True, exist_ok=True)
    for name, desc in skills.items():
        d = root / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_skill_md(name, desc))
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(*a):
        subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, env=dict(env))

    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, env=dict(env))
    (root / "README.md").write_text("repo\n")  # a commit needs at least one file
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return str(root)


def _zip_bytes(skills: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, desc in skills.items():
            zf.writestr(f"{name}/SKILL.md", _skill_md(name, desc))
    return buf.getvalue()


def _upload(skills: dict[str, str]):
    return {"file": ("skills.zip", _zip_bytes(skills), "application/zip")}


def _write_profile_skill(paths, pid, name, description="owned here"):
    """Put a Profile-owned skill on disk under this profile's skills dir — the second
    path into the profile projection, and the only origin the profile may delete."""
    d = paths.profile_dir(pid) / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_skill_md(name, description))


def _assert_rows(rows, keys, *, at_least=0):
    assert len(rows) >= at_least
    for row in rows:
        assert set(row) == keys, row


# --------------------------------------------------------------------------- #
#  The install-wide surface                                                    #
# --------------------------------------------------------------------------- #


def test_skill_list_rows_carry_exactly_the_install_wide_fields(app_client):
    """GET /api/skills. Never empty in practice — the bundled skills ship with the
    install — but the row must NOT leak the two per-profile fields: nothing
    install-wide resolved them."""
    client, _pid = app_client
    rows = client.get("/api/skills").json()["skills"]
    _assert_rows(rows, SKILL_KEYS, at_least=1)
    assert {r["origin"] for r in rows} <= {"bundled", "global"}


def test_state_and_delete_echo_the_refreshed_projection(app_client, tmp_path):
    """POST /api/skills/{name}/state and DELETE /api/skills/{name} answer ok plus the
    whole projection. Delete needs a Global skill to remove — a Bundled one is 409 —
    so install one first, which also covers the install envelope's populated state."""
    client, _pid = app_client
    installed = client.post("/api/skills/install", json={"install_id": "me/standalone"})
    assert installed.status_code == 200
    body = installed.json()
    assert body["ok"] is True
    _assert_rows(body["installed"], DISCOVERED_KEYS, at_least=1)
    _assert_rows(body["skills"], SKILL_KEYS, at_least=1)

    toggled = client.post("/api/skills/standalone/state", json={"enabled": False}).json()
    assert toggled["ok"] is True
    _assert_rows(toggled["skills"], SKILL_KEYS, at_least=1)

    removed = client.delete("/api/skills/standalone").json()
    assert removed["ok"] is True
    _assert_rows(removed["skills"], SKILL_KEYS, at_least=1)
    assert "standalone" not in {r["name"] for r in removed["skills"]}


def test_search_answers_the_same_shape_empty_and_populated(paths):
    """POST /api/skills/search. The empty state is a query the registry matches
    nothing for — a list, not a different body — and a hit carries the install_id the
    install route needs back."""
    app, _pid = make_profile_app(paths, persist=True, skills_client=_registry(hits=[]))
    with TestClient(app) as client:
        assert client.post("/api/skills/search", json={"query": "nothing"}).json() == {
            "results": []
        }

    app, _pid = make_profile_app(paths, persist=True, skills_client=_registry())
    with TestClient(app) as client:
        hits = client.post("/api/skills/search", json={"query": "standalone"}).json()["results"]
        _assert_rows(hits, SEARCH_HIT_KEYS, at_least=1)
        assert hits[0]["install_id"]


def test_discover_answers_the_same_shape_for_git_and_upload(app_client, tmp_path):
    """The two install-wide discover routes. A discover touches no state, so both
    scopes answer this one shape.

    There is no empty SUCCESS body to test here: a source holding no SKILL.md is a
    400 ``ErrorBody`` (``discover_source`` refuses it), so "nothing found" never
    reaches ``SkillDiscovered`` at all.
    """
    client, _pid = app_client
    empty_repo = _git_repo(tmp_path / "empty", {})
    nothing = client.post("/api/skills/discover", json={"git_url": empty_repo})
    assert nothing.status_code == 400
    assert set(nothing.json()) == {"error"}
    assert client.post("/api/skills/discover-upload", files=_upload({})).status_code == 400

    repo = _git_repo(tmp_path / "repo", {"alpha": "first", "beta": "second"})
    found = client.post("/api/skills/discover", json={"git_url": repo}).json()["skills"]
    _assert_rows(found, DISCOVERED_KEYS, at_least=2)

    uploaded = client.post("/api/skills/discover-upload", files=_upload({"gamma": "third"}))
    _assert_rows(uploaded.json()["skills"], DISCOVERED_KEYS, at_least=1)


def test_install_upload_reports_rows_beside_the_projection(app_client):
    """POST /api/skills/install-upload — the same envelope as the registry install,
    down a different code path (``install_from_source`` over an unpacked zip)."""
    client, _pid = app_client
    r = client.post(
        "/api/skills/install-upload",
        files=_upload({"zipped": "from a zip"}),
        data={"names": "zipped"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    _assert_rows(body["installed"], DISCOVERED_KEYS, at_least=1)
    _assert_rows(body["skills"], SKILL_KEYS, at_least=1)
    assert "zipped" in {s["name"] for s in body["skills"]}


# --------------------------------------------------------------------------- #
#  The profile surface                                                         #
# --------------------------------------------------------------------------- #


def test_profile_rows_carry_the_two_extra_fields_on_every_origin(paths):
    """GET /api/p/{pid}/skills. An inherited row and a profile-owned row reach the
    projection down two different code paths, and both must carry ``suppressed`` and
    ``available`` — the fields that make this projection worth a separate model."""
    app, pid = make_profile_app(paths, persist=True, skills_client=_registry())
    _write_profile_skill(paths, pid, "mine")
    with TestClient(app) as client:
        rows = client.get(api(pid, "/skills")).json()["skills"]
        _assert_rows(rows, PROFILE_SKILL_KEYS, at_least=2)
        by_name = {r["name"]: r for r in rows}
        assert by_name["mine"]["origin"] == "profile"
        assert {r["origin"] for r in rows} <= {"bundled", "global", "profile"}


def test_profile_state_routes_echo_the_profile_projection(paths):
    """The four per-profile state routes — suppress, un-suppress, own-skill state and
    delete — all answer ok plus the profile's own rows, never the install-wide ones."""
    app, pid = make_profile_app(paths, persist=True, skills_client=_registry())
    _write_profile_skill(paths, pid, "mine")
    with TestClient(app) as client:
        inherited = next(
            r["name"]
            for r in client.get(api(pid, "/skills")).json()["skills"]
            if r["name"] != "mine"
        )
        for call in (
            lambda: client.post(api(pid, f"/skills/{inherited}/suppress")),
            lambda: client.delete(api(pid, f"/skills/{inherited}/suppress")),
            lambda: client.post(api(pid, "/skills/mine/state"), json={"enabled": False}),
            lambda: client.delete(api(pid, "/skills/mine")),
        ):
            body = call().json()
            assert body["ok"] is True
            _assert_rows(body["skills"], PROFILE_SKILL_KEYS, at_least=1)


def test_profile_install_routes_answer_profile_rows(paths, tmp_path):
    """The profile's two install routes carry the same ``installed`` rows as the
    Global ones, but the projection beside them is this profile's — the difference
    the two install models exist to keep."""
    app, pid = make_profile_app(paths, persist=True, skills_client=_registry())
    with TestClient(app) as client:
        registry = client.post(api(pid, "/skills/install"), json={"install_id": "me/standalone"})
        assert registry.status_code == 200
        body = registry.json()
        assert body["ok"] is True
        _assert_rows(body["installed"], DISCOVERED_KEYS, at_least=1)
        _assert_rows(body["skills"], PROFILE_SKILL_KEYS, at_least=1)
        assert body["skills"][-1]["origin"] == "profile"  # ordered bundled → global → profile

        upload = client.post(
            api(pid, "/skills/install-upload"),
            files=_upload({"zipped": "from a zip"}),
            data={"names": "zipped"},
        )
        assert upload.status_code == 200
        _assert_rows(upload.json()["installed"], DISCOVERED_KEYS, at_least=1)
        _assert_rows(upload.json()["skills"], PROFILE_SKILL_KEYS, at_least=1)


def test_profile_discover_routes_answer_the_shared_shape(app_client, tmp_path):
    """The profile's discover pair installs nothing, so it answers the same
    ``SkillDiscovered`` body the install-wide pair does — down to refusing an empty
    source with a 400 rather than an empty list."""
    client, pid = app_client
    empty_repo = _git_repo(tmp_path / "empty", {})
    nothing = client.post(api(pid, "/skills/discover"), json={"git_url": empty_repo})
    assert nothing.status_code == 400
    assert client.post(api(pid, "/skills/discover-upload"), files=_upload({})).status_code == 400

    repo = _git_repo(tmp_path / "repo", {"alpha": "first"})
    found = client.post(api(pid, "/skills/discover"), json={"git_url": repo}).json()["skills"]
    _assert_rows(found, DISCOVERED_KEYS, at_least=1)
    uploaded = client.post(api(pid, "/skills/discover-upload"), files=_upload({"gamma": "third"}))
    _assert_rows(uploaded.json()["skills"], DISCOVERED_KEYS, at_least=1)


# --------------------------------------------------------------------------- #
#  The install-wide permission store                                           #
# --------------------------------------------------------------------------- #


def test_permission_snapshot_is_empty_on_a_fresh_install(app_client):
    """GET /api/permissions. Nothing is granted until someone grants it, and an empty
    list is the answer — not an absent key."""
    client, _pid = app_client
    assert client.get("/api/permissions").json() == {"commands": []}


def test_grant_and_revoke_echo_the_refreshed_snapshot(app_client):
    """POST/DELETE /api/permissions/commands answer ok plus the snapshot, so the
    settings list re-renders from the response rather than re-fetching. The rule
    string is built server-side, so the revoke has to name the canonical form."""
    client, _pid = app_client
    granted = client.post(
        "/api/permissions/commands", json={"tool": "run_shell_command", "prefix": "git"}
    )
    assert granted.status_code == 200
    body = granted.json()
    assert body["ok"] is True
    assert body["commands"] == ["run_shell_command(git *)"]
    assert client.get("/api/permissions").json() == {"commands": ["run_shell_command(git *)"]}

    revoked = client.request(
        "DELETE", "/api/permissions/commands", json={"rule": "run_shell_command(git *)"}
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"ok": True, "commands": []}
