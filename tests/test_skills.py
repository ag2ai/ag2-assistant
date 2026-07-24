"""Tests for the skills plugin + registry install wiring."""

import tempfile

import pytest
from ag2.tools.skills import LocalRuntime

from assistant.agent import (
    ask,
    build_skills_install_tools,
    build_skills_plugin,
    build_skills_runtime,
    bundled_skills_dir,
    create_agent,
)
from assistant.config import Config
from assistant.skills import DISABLE_OWN, SkillStateStore


def _write_skill(skills_dir, name, description):
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"
    )


def test_skills_runtime_builds_and_creates_dir(tmp_path):
    config = Config()
    config.skills_dir = tmp_path / "skills"
    runtime = build_skills_runtime(config)
    assert runtime is not None
    assert config.skills_dir.exists()


def test_skills_plugin_injects_catalog(tmp_path):
    """SkillPlugin surfaces bundled skills via the <available_skills> prompt block."""
    config = Config()
    config.skills_dir = tmp_path / "skills"
    runtime = build_skills_runtime(config)
    plugin = build_skills_plugin(config, runtime)
    assert type(plugin).__name__ == "Plugin"
    # Bundled skills are present, so the plugin contributes a catalog + tools.
    prompt = "\n".join(plugin._system_prompt)
    assert "<available_skills>" in prompt
    tool_names = {t.name for t in plugin._tools}
    assert "load_skill" in tool_names


def test_bundled_skills_are_discoverable(tmp_path):
    """First-party skills ship with AG2 Assistant and are available without installing."""

    d = bundled_skills_dir()
    assert d.exists()
    rt = LocalRuntime(dir=tempfile.mkdtemp(), extra_paths=[str(d)])
    discovered = rt.skills  # AG2 main renamed discover() → the `skills` accessor
    names = {m.name for m in discovered}
    assert {"web-research", "pdf-tools", "email-drafting", "self-knowledge"} <= names
    # description moved under metadata in AG2 main's Skill model
    assert all(m.metadata.description for m in discovered)  # required for disclosure


def test_disabled_skill_absent_from_catalog_then_restored(tmp_path):
    """The resolution seam (ADR 0016): a Disabled skill drops out of
    <available_skills> on the next build; re-enabling brings it back."""
    config = Config()
    config.skills_dir = tmp_path / "skills"
    # Point the install-wide state store at a known file for this test.
    config.root_dir = tmp_path / "root"
    store = SkillStateStore(config.root_dir / "skills.json")

    def catalog() -> str:
        runtime = build_skills_runtime(config)
        return "\n".join(build_skills_plugin(config, runtime)._system_prompt)

    assert "web-research" in catalog()  # present by default (default-on)

    store.set_enabled("web-research", False)
    prompt = catalog()
    assert "web-research" not in prompt
    assert "pdf-tools" in prompt  # only the disabled skill leaves the catalog

    store.set_enabled("web-research", True)
    assert "web-research" in catalog()  # re-enable restores it


def test_suppressed_skill_absent_from_one_profiles_catalog(tmp_path):
    """The resolution seam is per-profile (ADR 0016 t02): a skill Suppressed for
    profile A leaves A's <available_skills> but stays in B's — build_skills_plugin
    keys resolution on config.data_dir.name (the profile id)."""
    root = tmp_path / "root"
    store = SkillStateStore(root / "skills.json")

    def catalog(pid: str) -> str:
        config = Config()
        config.root_dir = root
        config.data_dir = root / "profiles" / pid  # .name == pid → resolution scope
        config.skills_dir = config.data_dir / "skills"
        runtime = build_skills_runtime(config)
        return "\n".join(build_skills_plugin(config, runtime)._system_prompt)

    assert "web-research" in catalog("work")  # default-on for everyone

    store.set_suppressed("web-research", "work", True)
    assert "web-research" not in catalog("work")  # gone for A only
    assert "pdf-tools" in catalog("work")  # siblings untouched
    assert "web-research" in catalog("personal")  # B still has it

    store.set_suppressed("web-research", "work", False)
    assert "web-research" in catalog("work")  # clearing restores it


def test_profile_skill_shadow_uses_own_state_in_catalog(tmp_path):
    """Same-named shared state cannot remove the winning Profile skill."""
    root = tmp_path / "root"
    config = Config()
    config.root_dir = root
    config.data_dir = root / "profiles" / "work"
    config.skills_dir = config.data_dir / "skills"
    _write_skill(root / "skills", "shadowed", "global copy")
    _write_skill(config.skills_dir, "shadowed", "profile copy")
    store = SkillStateStore(root / "skills.json")

    def catalog() -> str:
        runtime = build_skills_runtime(config)
        return "\n".join(build_skills_plugin(config, runtime)._system_prompt)

    store.set_enabled("shadowed", False)
    assert "shadowed" in catalog()

    store.set_suppressed("shadowed", "work", True)
    assert "shadowed" in catalog()

    store.set_suppressed("shadowed", "work", True, kind=DISABLE_OWN)
    assert "shadowed" not in catalog()


def test_profile_catalog_inherits_global_skill(tmp_path):
    """A Global skill is available to a profile that has no same-named copy."""
    root = tmp_path / "root"
    config = Config()
    config.root_dir = root
    config.data_dir = root / "profiles" / "work"
    config.skills_dir = config.data_dir / "skills"
    _write_skill(root / "skills", "shared-skill", "global copy")

    runtime = build_skills_runtime(config)
    prompt = "\n".join(build_skills_plugin(config, runtime)._system_prompt)

    assert "shared-skill" in prompt


def test_installed_skill_appears_in_catalog_after_rebuild(tmp_path):
    """ADR 0017 t04/t05: a freshly installed skill is in the agent's <available_skills>
    on the next build. Drives the real install path (install_from_source over a zip) into
    the profile's skills_dir, then rebuilds the plugin — the same rebuild the routes
    trigger via reload."""
    import io
    import zipfile

    from assistant.skills_install import install_from_source

    config = Config()
    config.skills_dir = tmp_path / "skills"

    def catalog() -> str:
        runtime = build_skills_runtime(config)
        return "\n".join(build_skills_plugin(config, runtime)._system_prompt)

    assert "freshly-installed" not in catalog()  # absent before install

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "freshly-installed/SKILL.md",
            "---\nname: freshly-installed\ndescription: a just-installed skill\n---\n# hi\n",
        )
    src = tmp_path / "up.zip"
    src.write_bytes(buf.getvalue())

    installed = install_from_source(
        build_skills_runtime(config), ["freshly-installed"], upload_path=src, filename="up.zip"
    )
    assert [r["name"] for r in installed] == ["freshly-installed"]
    assert "freshly-installed" in catalog()  # present after the next build


def test_registry_install_tools_exposed(tmp_path):
    """The skills.sh search/install/remove tools ride alongside the plugin."""
    config = Config()
    config.skills_dir = tmp_path / "skills"
    runtime = build_skills_runtime(config)
    tools = build_skills_install_tools(config, runtime)
    names = {t.name for t in tools}
    assert {"search_skills", "install_skill", "remove_skill"} == names


def test_agent_with_skills_builds(tmp_path):
    config = Config()
    config.skills_dir = tmp_path / "skills"
    agent = create_agent(config, memory=False, skills=True)
    assert agent is not None


def test_agent_without_skills_builds(tmp_path):
    config = Config()
    config.skills_dir = tmp_path / "skills"
    agent = create_agent(config, memory=False, skills=False)
    assert agent is not None


@pytest.mark.integration
async def test_agent_can_search_skills(tmp_path):
    """Integration: the agent searches the registry for a skill (hits skills.sh)."""

    config = Config()
    config.skills_dir = tmp_path / "skills"
    response = await ask(
        "Search the skills registry for a skill about PDFs. "
        "Just list any skill names you find; don't install anything.",
        config=config,
        memory=False,
    )
    assert isinstance(response, str)
    assert len(response) > 0
