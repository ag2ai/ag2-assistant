"""Tests for the skills plugin + registry install wiring."""

import pytest

from assistant.agent import (
    build_skills_install_tools,
    build_skills_plugin,
    build_skills_runtime,
    create_agent,
)
from assistant.config import Config


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
    import tempfile

    from ag2.tools.skills import LocalRuntime

    from assistant.agent import bundled_skills_dir

    d = bundled_skills_dir()
    assert d.exists()
    rt = LocalRuntime(dir=tempfile.mkdtemp(), extra_paths=[str(d)])
    discovered = rt.skills  # AG2 main renamed discover() → the `skills` accessor
    names = {m.name for m in discovered}
    assert {"web-research", "pdf-tools", "email-drafting"} <= names
    # description moved under metadata in AG2 main's Skill model
    assert all(m.metadata.description for m in discovered)  # required for disclosure


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
    from assistant.agent import ask

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
