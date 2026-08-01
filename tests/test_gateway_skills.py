"""Install-wide /api/skills routes — the Application → Skills projection + toggle (ADR 0016)."""

from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from tests.support.apps import make_manager, make_profile_app
from tests.support.fakes import skill_catalog_factory


def _client(paths):
    app, pid = make_profile_app(paths, persist=True)
    return TestClient(app), pid


def test_get_skills_projects_bundled_with_enabled(paths):
    """GET /api/skills lists the bundled first-party skills, each marked enabled by
    default (default-on) with origin=bundled."""
    client, _pid = _client(paths)
    with client:
        skills = client.get("/api/skills").json()["skills"]
        by_name = {s["name"]: s for s in skills}
        assert {"web-research", "pdf-tools", "email-drafting"} <= set(by_name)
        wr = by_name["web-research"]
        assert wr["origin"] == "bundled"
        assert wr["enabled"] is True
        assert wr["description"]  # description surfaced for the row


def test_state_toggle_flips_enabled(paths):
    client, _pid = _client(paths)
    with client:
        r = client.post("/api/skills/web-research/state", json={"enabled": False})
        assert r.status_code == 200
        by_name = {s["name"]: s for s in r.json()["skills"]}
        assert by_name["web-research"]["enabled"] is False
        assert by_name["pdf-tools"]["enabled"] is True  # only the toggled skill flips
        # The change is visible on a fresh GET, and re-enabling restores it.
        assert not next(
            s for s in client.get("/api/skills").json()["skills"] if s["name"] == "web-research"
        )["enabled"]
        client.post("/api/skills/web-research/state", json={"enabled": True})
        assert next(
            s for s in client.get("/api/skills").json()["skills"] if s["name"] == "web-research"
        )["enabled"]


def test_state_unknown_skill_404(paths):
    client, _pid = _client(paths)
    with client:
        r = client.post("/api/skills/does-not-exist/state", json={"enabled": False})
        assert r.status_code == 404


def test_state_toggle_reflected_in_resolved_catalog(paths):
    """Disabling install-wide makes the skill resolve unavailable for the profile's
    agent — asserted via the resolution seam the build uses."""
    from assistant.skills import SkillStateStore

    client, _pid = _client(paths)
    with client:
        client.post("/api/skills/pdf-tools/state", json={"enabled": False})
        store = SkillStateStore(paths.root / "skills.json")
        assert store.is_available("pdf-tools") is False
        assert store.is_available("web-research") is True


def test_state_toggle_fans_out_to_all_runtimes(paths):
    """An install-wide toggle reloads EVERY live runtime, so the disabled skill leaves
    the catalog everywhere at once — including profiles nobody is chatting with."""
    agents: dict[str, list] = {}
    manager = make_manager(paths, agent_factory=skill_catalog_factory(agents))
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        for pid in ("work", "personal"):
            assert "email-drafting" in agents[pid][-1].catalog

        r = client.post("/api/skills/email-drafting/state", json={"enabled": False})
        assert r.json()["ok"]
        for pid in ("work", "personal"):
            # rebuilt, and the rebuild resolved the new state
            assert "email-drafting" not in agents[pid][-1].catalog
            assert "web-research" in agents[pid][-1].catalog  # only the toggled skill goes
