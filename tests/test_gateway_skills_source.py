"""Install from a git URL or a local upload — discover-and-pick (ADR 0017 t05).

Git is exercised against a real local repo (``git clone`` accepts a path), and upload
against a real zip built in the test — no network. Covers: discover returns the full
multi-skill checklist; install lands exactly the selected subset into the right target;
an invalid source errors cleanly with nothing half-installed; a collision replaces.
"""

import io
import subprocess
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.config import load_config
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import api, make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def _skill_md(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\nBody of {name}.\n"


def _make_git_repo(root: Path, skills: dict[str, str]) -> str:
    """A real local git repo with one dir per skill under skills/. Returns its path
    (git clone --depth 1 accepts a local path as the 'URL')."""
    root.mkdir(parents=True, exist_ok=True)
    for name, desc in skills.items():
        d = root / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_skill_md(name, desc))
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*a):
        subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, env=dict(env))

    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, env=dict(env))
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return str(root)


def _make_zip(skills: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, desc in skills.items():
            zf.writestr(f"{name}/SKILL.md", _skill_md(name, desc))
    return buf.getvalue()


# --- git discover + install -----------------------------------------------------


def test_discover_git_returns_full_checklist(monkeypatch, tmp_path):
    client, _pid = _client(monkeypatch)
    with client:
        url = _make_git_repo(tmp_path / "repo", {"alpha": "the alpha", "beta": "the beta"})
        r = client.post("/api/skills/discover", json={"git_url": url})
        assert r.status_code == 200, r.text
        names = {s["name"]: s["description"] for s in r.json()["skills"]}
        assert names == {"alpha": "the alpha", "beta": "the beta"}


def test_install_git_subset_lands_only_selected(monkeypatch, tmp_path):
    client, _pid = _client(monkeypatch)
    with client:
        url = _make_git_repo(tmp_path / "repo", {"alpha": "a", "beta": "b", "gamma": "g"})
        r = client.post("/api/skills/install", json={"git_url": url, "names": ["alpha", "gamma"]})
        assert r.status_code == 200, r.text
        installed = {s["name"] for s in r.json()["installed"]}
        assert installed == {"alpha", "gamma"}
        catalog = {s["name"] for s in client.get("/api/skills").json()["skills"]}
        assert {"alpha", "gamma"} <= catalog
        assert "beta" not in catalog  # not selected → not installed


def test_discover_unreachable_git_is_400(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/discover", json={"git_url": "/nonexistent/repo/path"})
        assert r.status_code == 400
        assert r.json()["error"]


def test_install_git_collision_replaces(monkeypatch, tmp_path):
    client, _pid = _client(monkeypatch)
    with client:
        url1 = _make_git_repo(tmp_path / "r1", {"dup": "first"})
        client.post("/api/skills/install", json={"git_url": url1, "names": ["dup"]})
        first = {s["name"]: s for s in client.get("/api/skills").json()["skills"]}
        assert first["dup"]["description"] == "first"

        url2 = _make_git_repo(tmp_path / "r2", {"dup": "second"})
        r = client.post("/api/skills/install", json={"git_url": url2, "names": ["dup"]})
        again = {s["name"]: s for s in r.json()["skills"]}
        assert again["dup"]["description"] == "second"
        assert sum(1 for s in r.json()["skills"] if s["name"] == "dup") == 1


def test_install_git_unknown_name_is_400_nothing_installed(monkeypatch, tmp_path):
    client, _pid = _client(monkeypatch)
    with client:
        url = _make_git_repo(tmp_path / "repo", {"alpha": "a"})
        r = client.post("/api/skills/install", json={"git_url": url, "names": ["ghost"]})
        assert r.status_code == 400
        assert "ghost" not in {s["name"] for s in client.get("/api/skills").json()["skills"]}


# --- upload discover + install --------------------------------------------------


def test_discover_upload_zip_returns_checklist(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        data = _make_zip({"u1": "upload one", "u2": "upload two"})
        r = client.post("/api/skills/discover-upload",
                        files={"file": ("skills.zip", data, "application/zip")})
        assert r.status_code == 200, r.text
        assert {s["name"] for s in r.json()["skills"]} == {"u1", "u2"}


def test_install_upload_zip_subset(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        data = _make_zip({"u1": "one", "u2": "two"})
        r = client.post("/api/skills/install-upload",
                        files={"file": ("skills.zip", data, "application/zip")},
                        data={"names": "u2"})
        assert r.status_code == 200, r.text
        assert {s["name"] for s in r.json()["installed"]} == {"u2"}
        catalog = {s["name"] for s in client.get("/api/skills").json()["skills"]}
        assert "u2" in catalog and "u1" not in catalog


def test_discover_upload_invalid_is_400(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/discover-upload",
                        files={"file": ("notes.txt", b"hello", "text/plain")})
        assert r.status_code == 400


def test_discover_git_rejects_ext_transport(monkeypatch):
    """git's ext:: transport runs a shell command at clone time (RCE on the host); the
    installer must refuse it before ever invoking git (finding 2)."""
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/discover", json={"git_url": "ext::sh -c 'id'"})
        assert r.status_code == 400
        assert "transport" in r.json()["error"].lower()


def test_discover_git_rejects_file_scheme(monkeypatch):
    """An explicit file:// scheme (local-read/SSRF variant) is off the allowlist; a bare
    local PATH still works (the other git tests rely on it)."""
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/discover", json={"git_url": "file:///etc/passwd"})
        assert r.status_code == 400
        assert "scheme" in r.json()["error"].lower()


def test_install_root_skill_excludes_nested_sibling(monkeypatch, tmp_path):
    """A source with a ROOT SKILL.md (A) plus sub/SKILL.md (B): installing only A must
    not drag B's files inside A's installed folder (finding 6)."""
    client, _pid = _client(monkeypatch)
    with client:
        repo = tmp_path / "repo"
        (repo).mkdir(parents=True)
        (repo / "SKILL.md").write_text(_skill_md("alpha", "root skill"))
        (repo / "sub").mkdir()
        (repo / "sub" / "SKILL.md").write_text(_skill_md("beta", "nested skill"))
        url = _make_git_repo(repo, {})  # commit the tree as-is (no extra skills/ dirs)

        r = client.post("/api/skills/install", json={"git_url": url, "names": ["alpha"]})
        assert r.status_code == 200, r.text
        installed_dir = load_config().skills_dir / "alpha"
        assert (installed_dir / "SKILL.md").exists()
        # B's subtree was pruned — no nested SKILL.md landed inside A.
        assert not list(installed_dir.rglob("sub/SKILL.md"))


def test_install_upload_rejects_zip_bomb(monkeypatch):
    """A zip whose header claims an enormous uncompressed size is refused before
    extractall writes a byte (finding 7)."""
    import io
    import zipfile as zf_mod

    client, _pid = _client(monkeypatch)
    with client:
        # Build a zip whose central-directory entry advertises a > per-file-cap size but
        # is cheap to store (highly compressible zeros).
        buf = io.BytesIO()
        with zf_mod.ZipFile(buf, "w", zf_mod.ZIP_DEFLATED) as zf:
            zf.writestr("skill/SKILL.md", b"\0" * (26 * 1024 * 1024))  # > 25 MB cap
        r = client.post("/api/skills/discover-upload",
                        files={"file": ("bomb.zip", buf.getvalue(), "application/zip")})
        assert r.status_code == 400
        assert "large" in r.json()["error"].lower()


def test_install_upload_into_profile(monkeypatch):
    """An upload install from the Profiles zone lands in the active profile only."""
    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=True)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        data = _make_zip({"pskill": "profile upload"})
        r = client.post(api("work", "/skills/install-upload"),
                        files={"file": ("s.zip", data, "application/zip")},
                        data={"names": "pskill"})
        assert r.status_code == 200, r.text
        w = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert w["pskill"]["origin"] == "profile"
        assert "pskill" not in {s["name"] for s in client.get(api("personal", "/skills")).json()["skills"]}
