"""Install from the skills.sh registry — target-by-surface (ADR 0017 t04).

The registry answers over a scripted HTTP transport (``ScriptedSkillsClient``), so
ag2's real search and download code runs: the tarball is a real ``.tar.gz``, hashed,
extracted and installed through the runtime exactly as the live path would. Asserts the
search projection, that a Global install lands install-wide + fans out, that a Profile
install lands only for that profile, and that a name collision replaces.
"""

import httpx
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from tests.support.apps import api, make_manager, make_profile_app
from tests.support.fakes import skill_catalog_factory
from tests.support.http import ScriptedSkillsClient
from tests.support.stubs import skill_tarball

_SEARCH_HITS = [
    {
        "name": "react-best-practices",
        "skillId": "react-best-practices",
        "source": "vercel-labs/agent-skills",
        "description": "React rules",
        "installs": 1234,
    },
    {
        "name": "standalone",
        "skillId": "",
        "source": "me/standalone",
        "description": "A standalone skill",
        "installs": 7,
    },
]


def _registry(*, skill="standalone", nested=False, desc="installed via registry", status=200):
    """A registry client whose two endpoints are scripted: skills.sh search returns the
    canned hits, and the GitHub tarball endpoint returns a real archive carrying
    ``skill`` (``nested`` for the monorepo layout). ``status`` other than 200 makes the
    download fail the way a missing repo does."""

    def handle(request: httpx.Request) -> httpx.Response:
        if "/search" in request.url.path:
            return httpx.Response(200, json={"skills": _SEARCH_HITS})
        if status != 200:  # /repos/{owner}/{repo}/tarball
            return httpx.Response(status, json={"message": "Not Found"})
        return httpx.Response(
            200,
            content=skill_tarball(skill, description=desc, nested=nested),
            headers={"content-type": "application/gzip"},
        )

    return ScriptedSkillsClient(handle)


def _client(paths, **kwargs):
    app, pid = make_profile_app(paths, persist=True, skills_client=_registry(), **kwargs)
    return TestClient(app), pid


def test_search_projects_results(paths):
    client, _pid = _client(paths)
    with client:
        r = client.post("/api/skills/search", json={"query": "react"})
        assert r.status_code == 200
        results = r.json()["results"]
        by_name = {s["name"]: s for s in results}
        assert (
            by_name["react-best-practices"]["install_id"]
            == "vercel-labs/agent-skills/react-best-practices"
        )
        assert by_name["react-best-practices"]["description"] == "React rules"
        assert by_name["react-best-practices"]["installs"] == 1234
        # A standalone repo (no skillId) → install_id is just the source.
        assert by_name["standalone"]["install_id"] == "me/standalone"


def test_search_failure_is_502(paths):
    """A dead registry surfaces a 502 instead of a 500 — the page stays usable."""

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("skills.sh is unreachable", request=request)

    app, _pid = make_profile_app(paths, persist=True, skills_client=ScriptedSkillsClient(dead))
    with TestClient(app) as client:
        r = client.post("/api/skills/search", json={"query": "react"})
        assert r.status_code == 502
        assert "search failed" in r.json()["error"]


def test_install_global_lands_and_fans_out(paths):
    """A Global install writes into the Global layer and rebuilds every runtime's agent,
    so the new skill is in every profile's catalog."""
    agents: dict[str, list] = {}
    manager = make_manager(paths, agent_factory=skill_catalog_factory(agents))
    app = create_app(manager, skills_client=_registry())
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        for pid in ("work", "personal"):
            assert "standalone" not in agents[pid][-1].catalog

        r = client.post("/api/skills/install", json={"install_id": "me/standalone"})
        assert r.status_code == 200, r.text
        assert r.json()["installed"][0]["name"] == "standalone"
        # In the install-wide projection as a Global skill...
        by_name = {s["name"]: s for s in r.json()["skills"]}
        assert by_name["standalone"]["origin"] == "global"
        # ...on disk in the Global layer, with the downloaded SKILL.md...
        installed = paths.skills_dir / "standalone" / "SKILL.md"
        assert "installed via registry" in installed.read_text()
        # ...and fanned out to every live runtime.
        for pid in ("work", "personal"):
            assert "standalone" in agents[pid][-1].catalog


def test_install_profile_lands_only_for_that_profile(paths):
    agents: dict[str, list] = {}
    manager = make_manager(paths, persist=True, agent_factory=skill_catalog_factory(agents))
    app = create_app(manager, skills_client=_registry())
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        r = client.post(api("work", "/skills/install"), json={"install_id": "me/standalone"})
        assert r.status_code == 200, r.text
        # Present for work as a profile-owned skill...
        w = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert w["standalone"]["origin"] == "profile"
        assert "standalone" in agents["work"][-1].catalog
        # ...absent for personal, whose agent was never rebuilt.
        p = {s["name"] for s in client.get(api("personal", "/skills")).json()["skills"]}
        assert "standalone" not in p
        assert len(agents["personal"]) == 1


def test_install_collision_replaces(paths):
    app, _pid = make_profile_app(paths, persist=True, skills_client=_registry(desc="first"))
    with TestClient(app) as client:
        client.post("/api/skills/install", json={"install_id": "me/standalone"})
        first = {s["name"]: s for s in client.get("/api/skills").json()["skills"]}
        assert first["standalone"]["description"] == "first"

    # Re-install the same name from a registry now serving a new description → replaces.
    app, _pid = make_profile_app(paths, persist=True, skills_client=_registry(desc="second"))
    with TestClient(app) as client:
        r = client.post("/api/skills/install", json={"install_id": "me/standalone"})
        again = {s["name"]: s for s in r.json()["skills"]}
        assert again["standalone"]["description"] == "second"
        # No duplicate row.
        assert sum(1 for s in r.json()["skills"] if s["name"] == "standalone") == 1


def test_install_from_a_monorepo_resolves_the_skill_subdir(paths):
    """``owner/repo/skill`` installs that skill's directory out of a repo of many —
    the extractor's monorepo path, not the standalone fallback."""
    registry = _registry(skill="react-best-practices", nested=True, desc="React rules")
    app, _pid = make_profile_app(paths, persist=True, skills_client=registry)
    with TestClient(app) as client:
        r = client.post(
            "/api/skills/install",
            json={"install_id": "vercel-labs/agent-skills/react-best-practices"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["installed"][0]["name"] == "react-best-practices"
        skill_md = paths.skills_dir / "react-best-practices" / "SKILL.md"
        assert "React rules" in skill_md.read_text()


def test_install_failure_is_400_and_nothing_installed(paths):
    """A registry 404 surfaces a clean 400 and leaves nothing behind."""
    app, _pid = make_profile_app(paths, persist=True, skills_client=_registry(status=404))
    with TestClient(app) as client:
        r = client.post("/api/skills/install", json={"install_id": "me/nope"})
        assert r.status_code == 400
        assert "not found" in r.json()["error"].lower()
        assert "nope" not in {s["name"] for s in client.get("/api/skills").json()["skills"]}
        assert not (paths.skills_dir / "nope").exists()


def test_install_without_source_is_400(paths):
    client, _pid = _client(paths)
    with client:
        r = client.post("/api/skills/install", json={})
        assert r.status_code == 400
