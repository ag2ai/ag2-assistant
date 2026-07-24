"""Delete skills — Global cascade (Application → Skills) + Profile (Profiles zone), ADR 0016 t03.

A Global delete removes the skill install-wide, cascade-purges every profile's
Suppression (so a same-named re-install is default-on everywhere), and fans out a
reload. A Profile delete removes only the active profile's own skill. A Bundled skill
can't be deleted (409).
"""

from fastapi.testclient import TestClient

from assistant.config import load_config
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from assistant.skills import SkillStateStore
from tests.conftest import api, make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def _write_skill(skills_dir, name, desc="a global skill"):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    return d


def test_delete_global_removes_from_projection(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        _write_skill(load_config().skills_dir, "extra-global")
        assert "extra-global" in {s["name"] for s in client.get("/api/skills").json()["skills"]}

        r = client.delete("/api/skills/extra-global")
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["skills"]}
        assert "extra-global" not in names
        # And it's gone on a fresh GET across the whole install.
        assert "extra-global" not in {s["name"] for s in client.get("/api/skills").json()["skills"]}


def test_delete_bundled_is_409(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        r = client.delete("/api/skills/web-research")  # first-party, read-only
        assert r.status_code == 409
        # Still present.
        assert "web-research" in {s["name"] for s in client.get("/api/skills").json()["skills"]}


def test_delete_unknown_is_404(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        assert client.delete("/api/skills/does-not-exist").status_code == 404


def test_delete_global_cascade_purges_suppressions(monkeypatch):
    """Deleting a Global skill clears every profile's Suppression of it: a same-named
    re-install resolves default-on for every profile (no lingering suppression)."""
    client, pid = _client(monkeypatch)
    with client:
        _write_skill(load_config().skills_dir, "shared-x")
        # Suppress it for this profile, then delete it install-wide.
        client.post(api(pid, "/skills/shared-x/suppress"))
        store = SkillStateStore(load_config().root_dir / "skills.json")
        assert store.is_suppressed("shared-x", pid) is True

        r = client.delete("/api/skills/shared-x")
        assert r.status_code == 200
        store = SkillStateStore(load_config().root_dir / "skills.json")
        assert store.is_suppressed("shared-x", pid) is False

        # Re-install the same name → default-on everywhere (no ghost suppression).
        _write_skill(load_config().skills_dir, "shared-x")
        by_name = {s["name"]: s for s in client.get(api(pid, "/skills")).json()["skills"]}
        assert by_name["shared-x"]["available"] is True
        assert by_name["shared-x"]["suppressed"] is False


def test_delete_global_fans_out(monkeypatch):
    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=False)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        _write_skill(load_config().skills_dir, "fan-skill")

        reloaded: list[str] = []
        orig = manager.reload

        async def spy(pid):
            reloaded.append(pid)
            return await orig(pid)

        monkeypatch.setattr(manager, "reload", spy)

        r = client.delete("/api/skills/fan-skill")
        assert r.json()["ok"]
        assert set(reloaded) == {"work", "personal"}


def test_delete_profile_skill_affects_only_active_profile(monkeypatch):
    """A Profile skill is deleted for the active profile only; the change reloads just
    that profile. A shared skill can't be deleted from the profile tab (409)."""
    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=True)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        a_cfg = client.app.state.profiles.get("work").config
        _write_skill(a_cfg.skills_dir, "work-only", "work's own skill")
        assert "work-only" in {
            s["name"] for s in client.get(api("work", "/skills")).json()["skills"]
        }

        reloaded: list[str] = []
        orig = manager.reload

        async def spy(pid):
            reloaded.append(pid)
            return await orig(pid)

        monkeypatch.setattr(manager, "reload", spy)

        r = client.delete(api("work", "/skills/work-only"))
        assert r.status_code == 200
        assert "work-only" not in {s["name"] for s in r.json()["skills"]}
        assert reloaded == ["work"]  # only the active profile

        # A shared Bundled skill isn't this profile's own → 409 from the profile tab.
        assert client.delete(api("work", "/skills/web-research")).status_code == 409


def test_delete_profile_skill_unknown_is_404(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        assert client.delete(api(pid, "/skills/nope")).status_code == 404


def test_delete_global_skill_when_name_differs_from_dir(monkeypatch):
    """A hand-placed Global skill in weather-helper/ whose frontmatter says name:
    weather lists as 'weather' with a Delete button. DELETE must resolve the real dir
    (not install_dir/'weather') so it's actually removable, not a 404 (finding 3)."""
    client, _pid = _client(monkeypatch)
    with client:
        d = load_config().skills_dir / "weather-helper"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: weather\ndescription: forecasts\n---\n# weather\n")
        assert "weather" in {s["name"] for s in client.get("/api/skills").json()["skills"]}

        r = client.delete("/api/skills/weather")
        assert r.status_code == 200, r.text
        assert "weather" not in {s["name"] for s in r.json()["skills"]}
        assert not d.exists()  # the real directory (weather-helper/) is gone


def test_global_delete_preserves_same_named_profile_own_disable(monkeypatch):
    """A Global 'foo' and a profile's OWN 'foo' can coexist. Disabling the own copy and
    then deleting the unrelated Global 'foo' must leave the own copy's off-state intact
    (finding 4) — it must not silently flip back to available."""
    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=True)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        _write_skill(load_config().skills_dir, "foo", "global foo")
        work_cfg = client.app.state.profiles.get("work").config
        _write_skill(work_cfg.skills_dir, "foo", "work's own foo")  # shadows the global here

        client.post(api("work", "/skills/foo/state"), json={"enabled": False})  # disable own foo
        rows = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert rows["foo"]["origin"] == "profile" and rows["foo"]["available"] is False

        assert client.delete("/api/skills/foo").status_code == 200  # delete the Global foo

        rows = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert rows["foo"]["origin"] == "profile"
        assert rows["foo"]["available"] is False  # own foo still disabled, not resurrected


def test_profile_copy_delete_keeps_shadowed_global_suppression(monkeypatch):
    """Work suppressed a Global 'foo', then installed its OWN 'foo' shadowing it.
    Deleting the own copy reveals the Global 'foo' — which must stay Suppressed for
    Work, not flip to available (finding 5)."""
    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=True)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        _write_skill(load_config().skills_dir, "foo", "global foo")
        client.post(api("work", "/skills/foo/suppress"))  # suppress the Global for Work

        work_cfg = client.app.state.profiles.get("work").config
        _write_skill(work_cfg.skills_dir, "foo", "work's own foo")  # shadow it with an own copy
        assert {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}["foo"][
            "origin"
        ] == "profile"

        assert client.delete(api("work", "/skills/foo")).status_code == 200  # delete the copy

        rows = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert rows["foo"]["origin"] in ("global", "bundled")  # the Global is revealed
        assert rows["foo"]["suppressed"] is True  # still suppressed for Work
        assert rows["foo"]["available"] is False
