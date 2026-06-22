"""Tests for the skills toolkit wiring."""

import pytest

from assistant.agent import build_skills_toolkit, create_agent
from assistant.config import Config


def test_skills_toolkit_builds_and_creates_dir(tmp_path):
    config = Config()
    config.skills_dir = tmp_path / "skills"
    toolkit = build_skills_toolkit(config)
    assert type(toolkit).__name__ == "SkillSearchToolkit"
    assert config.skills_dir.exists()


def test_bundled_skills_are_discoverable(tmp_path):
    """First-party skills ship with AG2 Assistant and are available without installing."""
    import tempfile

    from autogen.beta.tools.skills import LocalRuntime

    from assistant.agent import bundled_skills_dir

    d = bundled_skills_dir()
    assert d.exists()
    rt = LocalRuntime(dir=tempfile.mkdtemp(), extra_paths=[str(d)])
    discovered = rt.skills  # AG2 main renamed discover() → the `skills` accessor
    names = {m.name for m in discovered}
    assert {"web-research", "pdf-tools", "email-drafting"} <= names
    # description moved under metadata in AG2 main's Skill model
    assert all(m.metadata.description for m in discovered)  # required for disclosure


def test_skills_toolkit_exposes_search_and_install(tmp_path):
    config = Config()
    config.skills_dir = tmp_path / "skills"
    toolkit = build_skills_toolkit(config)
    for tool in ("search_skills", "install_skill", "list_skills", "load_skill"):
        assert hasattr(toolkit, tool), f"missing {tool}"


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
