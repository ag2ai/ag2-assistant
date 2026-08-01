"""Delete skills — Global cascade (Application → Skills) + Profile (Profiles zone), ADR 0016 t03.

A Global delete removes the skill install-wide, cascade-purges every profile's
Suppression (so a same-named re-install is default-on everywhere), and fans out a
reload. A Profile delete removes only the active profile's own skill. A Bundled skill
can't be deleted (409).
"""

from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.skills import SkillStateStore
from tests.support.apps import api, make_manager, make_profile_app
from tests.support.fakes import skill_catalog_factory


def _client(paths):
    app, pid = make_profile_app(paths, persist=True)
    return TestClient(app), pid


def _write_skill(skills_dir, name, desc="a global skill"):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    return d


def test_delete_global_removes_from_projection(paths):
    client, _pid = _client(paths)
    with client:
        _write_skill(paths.skills_dir, "extra-global")
        assert "extra-global" in {s["name"] for s in client.get("/api/skills").json()["skills"]}

        r = client.delete("/api/skills/extra-global")
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["skills"]}
        assert "extra-global" not in names
        # And it's gone on a fresh GET across the whole install.
        assert "extra-global" not in {s["name"] for s in client.get("/api/skills").json()["skills"]}


def test_delete_bundled_is_409(paths):
    client, _pid = _client(paths)
    with client:
        r = client.delete("/api/skills/web-research")  # first-party, read-only
        assert r.status_code == 409
        # Still present.
        assert "web-research" in {s["name"] for s in client.get("/api/skills").json()["skills"]}


def test_delete_unknown_is_404(paths):
    client, _pid = _client(paths)
    with client:
        assert client.delete("/api/skills/does-not-exist").status_code == 404


def test_delete_global_cascade_purges_suppressions(paths):
    """Deleting a Global skill clears every profile's Suppression of it: a same-named
    re-install resolves default-on for every profile (no lingering suppression)."""
    client, pid = _client(paths)
    with client:
        _write_skill(paths.skills_dir, "shared-x")
        # Suppress it for this profile, then delete it install-wide.
        client.post(api(pid, "/skills/shared-x/suppress"))
        store = SkillStateStore(paths.root / "skills.json")
        assert store.is_suppressed("shared-x", pid) is True

        r = client.delete("/api/skills/shared-x")
        assert r.status_code == 200
        store = SkillStateStore(paths.root / "skills.json")
        assert store.is_suppressed("shared-x", pid) is False

        # Re-install the same name → default-on everywhere (no ghost suppression).
        _write_skill(paths.skills_dir, "shared-x")
        by_name = {s["name"]: s for s in client.get(api(pid, "/skills")).json()["skills"]}
        assert by_name["shared-x"]["available"] is True
        assert by_name["shared-x"]["suppressed"] is False


def test_delete_global_fans_out(paths):
    """A Global delete rebuilds every runtime's agent, so the skill leaves every
    profile's catalog — not just the projection on disk."""
    agents: dict[str, list] = {}
    manager = make_manager(paths, agent_factory=skill_catalog_factory(agents))
    app = create_app(manager)
    with TestClient(app) as client:
        _write_skill(paths.skills_dir, "fan-skill")  # before boot, so both agents see it
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        for pid in ("work", "personal"):
            assert "fan-skill" in agents[pid][-1].catalog

        r = client.delete("/api/skills/fan-skill")
        assert r.json()["ok"]
        for pid in ("work", "personal"):
            assert "fan-skill" not in agents[pid][-1].catalog
            assert "web-research" in agents[pid][-1].catalog  # only the deleted one goes


def test_delete_profile_skill_affects_only_active_profile(paths):
    """A Profile skill is deleted for the active profile only; the change reloads just
    that profile — Personal's agent is never rebuilt. A shared skill can't be deleted
    from the profile tab (409)."""
    agents: dict[str, list] = {}
    manager = make_manager(paths, persist=True, agent_factory=skill_catalog_factory(agents))
    app = create_app(manager)
    with TestClient(app) as client:
        # Placed before boot (the profile dir is derived from the id), so Work's agent
        # is built with it and the delete's rebuild is what takes it away.
        _write_skill(paths.profile_dir("work") / "skills", "work-only", "work's own skill")
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        assert "work-only" in agents["work"][-1].catalog
        assert "work-only" not in agents["personal"][-1].catalog  # profile-owned, not shared

        r = client.delete(api("work", "/skills/work-only"))
        assert r.status_code == 200
        assert "work-only" not in {s["name"] for s in r.json()["skills"]}
        assert "work-only" not in agents["work"][-1].catalog  # rebuilt without it
        assert len(agents["personal"]) == 1  # only the active profile reloaded

        # A shared Bundled skill isn't this profile's own → 409 from the profile tab.
        assert client.delete(api("work", "/skills/web-research")).status_code == 409


def test_delete_profile_skill_unknown_is_404(paths):
    client, pid = _client(paths)
    with client:
        assert client.delete(api(pid, "/skills/nope")).status_code == 404


def test_delete_global_skill_when_name_differs_from_dir(paths):
    """A hand-placed Global skill in weather-helper/ whose frontmatter says name:
    weather lists as 'weather' with a Delete button. DELETE must resolve the real dir
    (not install_dir/'weather') so it's actually removable, not a 404 (finding 3)."""
    client, _pid = _client(paths)
    with client:
        d = paths.skills_dir / "weather-helper"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: weather\ndescription: forecasts\n---\n# weather\n")
        assert "weather" in {s["name"] for s in client.get("/api/skills").json()["skills"]}

        r = client.delete("/api/skills/weather")
        assert r.status_code == 200, r.text
        assert "weather" not in {s["name"] for s in r.json()["skills"]}
        assert not d.exists()  # the real directory (weather-helper/) is gone


def test_global_delete_preserves_same_named_profile_own_disable(paths):
    """A Global 'foo' and a profile's OWN 'foo' can coexist. Disabling the own copy and
    then deleting the unrelated Global 'foo' must leave the own copy's off-state intact
    (finding 4) — it must not silently flip back to available."""
    manager = make_manager(paths, persist=True)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        _write_skill(paths.skills_dir, "foo", "global foo")
        work_cfg = client.app.state.profiles.get("work").config
        _write_skill(work_cfg.skills_dir, "foo", "work's own foo")  # shadows the global here

        client.post(api("work", "/skills/foo/state"), json={"enabled": False})  # disable own foo
        rows = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert rows["foo"]["origin"] == "profile" and rows["foo"]["available"] is False

        assert client.delete("/api/skills/foo").status_code == 200  # delete the Global foo

        rows = {s["name"]: s for s in client.get(api("work", "/skills")).json()["skills"]}
        assert rows["foo"]["origin"] == "profile"
        assert rows["foo"]["available"] is False  # own foo still disabled, not resurrected


def test_profile_copy_delete_keeps_shadowed_global_suppression(paths):
    """Work suppressed a Global 'foo', then installed its OWN 'foo' shadowing it.
    Deleting the own copy reveals the Global 'foo' — which must stay Suppressed for
    Work, not flip to available (finding 5)."""
    manager = make_manager(paths, persist=True)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        _write_skill(paths.skills_dir, "foo", "global foo")
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
