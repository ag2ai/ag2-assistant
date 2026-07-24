"""Per-profile /api/p/{pid}/skills routes — the Profiles zone Skills tab (ADR 0016 t02).

Covers the resolved projection, Suppress/un-suppress of an inherited (Bundled) skill,
a Profile-owned skill's per-profile Enable/Disable, that install-wide Disable reads
through here too, and that a per-profile change reloads ONLY the active profile.
"""

from fastapi.testclient import TestClient

from assistant.config import load_config
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import api, make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def _write_skill(skills_dir, name, description):
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"
    )


def test_profile_projection_has_origin_enabled_suppressed_available(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        skills = client.get(api(pid, "/skills")).json()["skills"]
        by_name = {s["name"]: s for s in skills}
        assert {"web-research", "pdf-tools", "email-drafting"} <= set(by_name)
        wr = by_name["web-research"]
        assert wr["origin"] == "bundled"
        assert wr["enabled"] is True  # install-wide on
        assert wr["suppressed"] is False  # inherit "on"
        assert wr["available"] is True  # resolved on
        assert wr["description"]


def test_suppress_flips_available_for_this_profile_only(monkeypatch):
    """POST /suppress turns a Bundled skill off for this profile; DELETE restores it.
    The install-wide state is never touched."""
    client, pid = _client(monkeypatch)
    with client:
        r = client.post(api(pid, "/skills/web-research/suppress"))
        assert r.status_code == 200
        by_name = {s["name"]: s for s in r.json()["skills"]}
        assert by_name["web-research"]["suppressed"] is True
        assert by_name["web-research"]["available"] is False
        assert by_name["pdf-tools"]["available"] is True  # only the one flips
        # install-wide GET still shows it enabled (suppression is per-profile)
        iw = {s["name"]: s for s in client.get("/api/skills").json()["skills"]}
        assert iw["web-research"]["enabled"] is True

        r = client.delete(api(pid, "/skills/web-research/suppress"))
        by_name = {s["name"]: s for s in r.json()["skills"]}
        assert by_name["web-research"]["suppressed"] is False
        assert by_name["web-research"]["available"] is True


def test_suppress_unknown_skill_404(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        assert client.post(api(pid, "/skills/nope/suppress")).status_code == 404
        assert client.delete(api(pid, "/skills/nope/suppress")).status_code == 404


def test_install_wide_disable_reads_unavailable_in_profile_view(monkeypatch):
    """A skill Disabled install-wide shows unavailable in the profile projection too —
    the two surfaces never contradict."""
    client, pid = _client(monkeypatch)
    with client:
        client.post("/api/skills/pdf-tools/state", json={"enabled": False})
        by_name = {s["name"]: s for s in client.get(api(pid, "/skills")).json()["skills"]}
        assert by_name["pdf-tools"]["enabled"] is False
        assert by_name["pdf-tools"]["available"] is False
        assert by_name["pdf-tools"]["suppressed"] is False  # off via install-wide, not suppression


def test_profile_owned_skill_state_scoped_to_profile(monkeypatch):
    """A skill in the profile's own skills_dir has origin=profile and Enable/Disable
    via /state — the Disable is a per-profile off-record, so a shared skill can't use it."""
    client, pid = _client(monkeypatch)
    with client:
        cfg = client.app.state.profiles.get(pid).config
        cfg.skills_dir.mkdir(parents=True, exist_ok=True)
        (cfg.skills_dir / "mine").mkdir(parents=True, exist_ok=True)
        (cfg.skills_dir / "mine" / "SKILL.md").write_text(
            "---\nname: mine\ndescription: my own skill\n---\n# mine\n"
        )
        by_name = {s["name"]: s for s in client.get(api(pid, "/skills")).json()["skills"]}
        assert "mine" in by_name
        assert by_name["mine"]["origin"] == "profile"
        assert by_name["mine"]["available"] is True

        # Disable it for this profile.
        r = client.post(api(pid, "/skills/mine/state"), json={"enabled": False})
        assert r.status_code == 200
        by_name = {s["name"]: s for s in r.json()["skills"]}
        assert by_name["mine"]["available"] is False
        assert by_name["mine"]["suppressed"] is True

        # A shared (Bundled) skill is NOT a Profile skill → /state 404s for it.
        assert (
            client.post(api(pid, "/skills/web-research/state"), json={"enabled": False}).status_code
            == 404
        )


def test_profile_skill_shadow_ignores_same_named_shared_off_state(monkeypatch):
    """A Profile skill wins the name clash and uses only its own Enabled state."""
    client, pid = _client(monkeypatch)
    with client:
        global_skills = load_config().skills_dir
        profile_skills = client.app.state.profiles.get(pid).config.skills_dir

        _write_skill(global_skills, "global-off", "global copy")
        _write_skill(profile_skills, "global-off", "profile copy")
        client.post("/api/skills/global-off/state", json={"enabled": False})

        _write_skill(global_skills, "shared-off", "global copy")
        client.post(api(pid, "/skills/shared-off/suppress"))
        _write_skill(profile_skills, "shared-off", "profile copy")

        rows = {s["name"]: s for s in client.get(api(pid, "/skills")).json()["skills"]}
        for name in ("global-off", "shared-off"):
            assert rows[name]["origin"] == "profile"
            assert rows[name]["enabled"] is True
            assert rows[name]["suppressed"] is False
            assert rows[name]["available"] is True


def test_per_profile_change_reloads_only_active_profile(monkeypatch):
    """Suppressing in one profile reloads ONLY that profile — never fans out."""
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

        r = client.post(api("work", "/skills/web-research/suppress"))
        assert r.json()["ok"]
        assert reloaded == ["work"]  # only the active profile, no fan-out
