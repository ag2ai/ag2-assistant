"""Install-wide /api/skills routes — the Application → Skills projection + toggle (ADR 0016)."""

from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def test_get_skills_projects_bundled_with_enabled(monkeypatch):
    """GET /api/skills lists the bundled first-party skills, each marked enabled by
    default (default-on) with origin=bundled."""
    client, _pid = _client(monkeypatch)
    with client:
        skills = client.get("/api/skills").json()["skills"]
        by_name = {s["name"]: s for s in skills}
        assert {"web-research", "pdf-tools", "email-drafting"} <= set(by_name)
        wr = by_name["web-research"]
        assert wr["origin"] == "bundled"
        assert wr["enabled"] is True
        assert wr["description"]  # description surfaced for the row


def test_state_toggle_flips_enabled(monkeypatch):
    client, _pid = _client(monkeypatch)
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


def test_state_unknown_skill_404(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/skills/does-not-exist/state", json={"enabled": False})
        assert r.status_code == 404


def test_state_toggle_reflected_in_resolved_catalog(monkeypatch):
    """Disabling install-wide makes the skill resolve unavailable for the profile's
    agent — asserted via the resolution seam the build uses."""
    from assistant.config import load_config
    from assistant.skills import SkillStateStore

    client, _pid = _client(monkeypatch)
    with client:
        client.post("/api/skills/pdf-tools/state", json={"enabled": False})
        store = SkillStateStore(load_config().root_dir / "skills.json")
        assert store.is_available("pdf-tools") is False
        assert store.is_available("web-research") is True


def test_state_toggle_fans_out_to_all_runtimes(monkeypatch):
    """An install-wide toggle reloads EVERY live runtime (observed via a spy) so the
    catalog changes everywhere at once — including background profiles."""
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

        r = client.post("/api/skills/email-drafting/state", json={"enabled": False})
        assert r.json()["ok"]
        assert set(reloaded) == {"work", "personal"}  # every runtime reloaded
