"""Install from the skills.sh registry — target-by-surface (ADR 0017 t04).

The registry is faked at the ``SkillsClient`` seam (no network): ``search`` returns
canned records; ``download_skill`` stages a real SKILL.md and installs it via the
runtime, exactly as the live path would. Asserts search projection, that a Global
install lands install-wide + fans out, a Profile install lands only for that profile,
and that a name collision replaces.
"""

import tempfile
from pathlib import Path

from ag2.exceptions import SkillDownloadError
from ag2.tools.skills.skill_types import SkillMetadata
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import api, make_profile_app, use_fake_agent


def _fake_registry(monkeypatch, *, desc="installed via registry"):
    """Patch SkillsClient so search/download don't hit the network. download_skill
    stages a SKILL.md named after the skill id's last segment and installs it."""
    import assistant.skills_install as si

    async def fake_search(self, query, limit=10):
        return [
            {"name": "react-best-practices", "skillId": "react-best-practices",
             "source": "vercel-labs/agent-skills", "description": "React rules", "installs": 1234},
            {"name": "standalone", "skillId": "", "source": "me/standalone",
             "description": "A standalone skill", "installs": 7},
        ]

    async def fake_download(self, source, skill_id, runtime):
        name = (skill_id or source.split("/")[-1]).split("/")[-1]
        with tempfile.TemporaryDirectory() as td:
            staged = Path(td) / name
            staged.mkdir(parents=True)
            (staged / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
            runtime.install(staged, name)
        return SkillMetadata(name=name, description=desc), "deadbeef"

    monkeypatch.setattr(si.SkillsClient, "search", fake_search)
    monkeypatch.setattr(si.SkillsClient, "download_skill", fake_download)


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def test_search_projects_results(monkeypatch):
    _fake_registry(monkeypatch)
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/search", json={"query": "react"})
        assert r.status_code == 200
        results = r.json()["results"]
        by_name = {s["name"]: s for s in results}
        assert by_name["react-best-practices"]["install_id"] == "vercel-labs/agent-skills/react-best-practices"
        assert by_name["react-best-practices"]["description"] == "React rules"
        assert by_name["react-best-practices"]["installs"] == 1234
        # A standalone repo (no skillId) → install_id is just the source.
        assert by_name["standalone"]["install_id"] == "me/standalone"


def test_install_global_lands_and_fans_out(monkeypatch):
    _fake_registry(monkeypatch)
    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=False)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        reloaded: list[str] = []
        orig = manager.reload

        async def spy(pid):
            reloaded.append(pid)
            return await orig(pid)

        monkeypatch.setattr(manager, "reload", spy)

        r = client.post("/api/skills/install",
                        json={"install_id": "vercel-labs/agent-skills/react-best-practices"})
        assert r.status_code == 200, r.text
        assert r.json()["installed"][0]["name"] == "react-best-practices"
        # In the install-wide projection as a Global skill.
        by_name = {s["name"]: s for s in r.json()["skills"]}
        assert by_name["react-best-practices"]["origin"] == "global"
        # Fanned out to every live runtime.
        assert set(reloaded) == {"work", "personal"}


def test_install_profile_lands_only_for_that_profile(monkeypatch):
    _fake_registry(monkeypatch)
    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=True)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        reloaded: list[str] = []
        orig = manager.reload

        async def spy(pid):
            reloaded.append(pid)
            return await orig(pid)

        monkeypatch.setattr(manager, "reload", spy)

        r = client.post(api("work", "/skills/install"),
                        json={"install_id": "me/standalone"})
        assert r.status_code == 200, r.text
        # Present for work as a profile-owned skill...
        w = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert w["standalone"]["origin"] == "profile"
        # ...absent for personal, and only work reloaded.
        p = {s["name"] for s in client.get(api("personal", "/skills")).json()["skills"]}
        assert "standalone" not in p
        assert reloaded == ["work"]


def test_install_collision_replaces(monkeypatch):
    _fake_registry(monkeypatch, desc="first")
    client, _pid = _client(monkeypatch)
    with client:
        client.post("/api/skills/install", json={"install_id": "me/standalone"})
        first = {s["name"]: s for s in client.get("/api/skills").json()["skills"]}
        assert first["standalone"]["description"] == "first"

        # Re-install the same name with a new description → replaces in place.
        _fake_registry(monkeypatch, desc="second")
        r = client.post("/api/skills/install", json={"install_id": "me/standalone"})
        again = {s["name"]: s for s in r.json()["skills"]}
        assert again["standalone"]["description"] == "second"
        # No duplicate row.
        assert sum(1 for s in r.json()["skills"] if s["name"] == "standalone") == 1


def test_install_failure_is_400_and_nothing_installed(monkeypatch):
    """A registry download failure surfaces a clean 400 and leaves nothing behind."""
    import assistant.skills_install as si

    async def boom(self, source, skill_id, runtime):
        raise SkillDownloadError("Skill not found: me/nope")

    monkeypatch.setattr(si.SkillsClient, "download_skill", boom)
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/install", json={"install_id": "me/nope"})
        assert r.status_code == 400
        assert "not found" in r.json()["error"].lower()
        assert "nope" not in {s["name"] for s in client.get("/api/skills").json()["skills"]}


def test_install_without_source_is_400(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/install", json={})
        assert r.status_code == 400
