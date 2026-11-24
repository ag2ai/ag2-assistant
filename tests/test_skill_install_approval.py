"""install_skill asks through the same command approval as run_code."""

import json

import httpx
import pytest
from ag2 import Agent
from ag2.events import ToolCallEvent
from ag2.testing import TestConfig
from ag2.tools import SkillSearchToolkit

from assistant.agent import build_skills_install_tools, build_skills_runtime
from assistant.config import Config
from assistant.permissions import (
    ALLOW_ONCE,
    DENY,
    PermissionManager,
    PermissionStore,
    always_allow_command_label,
)
from tests.support.http import ScriptedSkillsClient
from tests.support.stubs import skill_tarball


class AnsweringAsker:
    """Answers every question with one fixed option and keeps what it was asked."""

    def __init__(self, answer: str):
        self.answer = answer
        self.questions: list = []

    async def ask(self, question, timeout=None):
        self.questions.append(question)
        return self.answer


def _registry() -> ScriptedSkillsClient:
    """A registry whose tarball endpoint serves a real one-skill archive."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=skill_tarball("standalone", description="from the registry"),
            headers={"content-type": "application/gzip"},
        )

    return ScriptedSkillsClient(handle)


def _scripted_toolkit(runtime) -> SkillSearchToolkit:
    toolkit = SkillSearchToolkit(runtime)
    toolkit._client = _registry()
    return toolkit


def _setup(paths, tmp_path):
    config = Config.for_paths(paths)
    config.skills_dir = tmp_path / "skills"
    runtime = build_skills_runtime(config)
    tools = build_skills_install_tools(config, runtime, toolkit_factory=_scripted_toolkit)
    return config, {t.name: t for t in tools}


async def _run(tool, arguments: dict, asker, tmp_path) -> None:
    """One agent turn that calls ``tool`` once, with ``asker`` behind the approval."""
    dependencies = {}
    if asker is not None:
        store = PermissionStore(path=tmp_path / "permissions.json")
        dependencies[PermissionManager] = PermissionManager(store, asker)
    config = TestConfig(ToolCallEvent(name=tool.name, arguments=json.dumps(arguments)), "done")
    agent = Agent("installer", config=config, tools=[tool], dependencies=dependencies)
    await agent.ask("go")


async def _install(tool, asker, tmp_path) -> None:
    await _run(tool, {"skill_id": "me/standalone"}, asker, tmp_path)


def _installed(config) -> bool:
    return (config.skills_dir / "standalone" / "SKILL.md").is_file()


async def test_denied_install_writes_nothing(paths, tmp_path):
    config, tools = _setup(paths, tmp_path)
    asker = AnsweringAsker(DENY)
    await _install(tools["install_skill"], asker, tmp_path)
    assert len(asker.questions) == 1 and asker.questions[0].kind == "permission"
    assert not _installed(config)
    runtime = build_skills_runtime(config)
    assert "standalone" not in {s.name for s in runtime.skills}


async def test_allowed_install_lands_in_skills_dir(paths, tmp_path):
    config, tools = _setup(paths, tmp_path)
    await _install(tools["install_skill"], AnsweringAsker(ALLOW_ONCE), tmp_path)
    assert _installed(config)


async def test_install_without_an_approver_is_blocked(paths, tmp_path):
    config, tools = _setup(paths, tmp_path)
    await _install(tools["install_skill"], None, tmp_path)
    assert not _installed(config)


async def test_install_offers_no_always_allow(paths, tmp_path):
    _config, tools = _setup(paths, tmp_path)
    asker = AnsweringAsker(DENY)
    await _install(tools["install_skill"], asker, tmp_path)
    assert always_allow_command_label("install_skill", None) not in asker.questions[0].options


def test_install_skill_refuses_a_blanket_grant(tmp_path):
    with pytest.raises(ValueError):
        PermissionStore(path=tmp_path / "permissions.json").grant_command("install_skill")


async def test_remove_stays_unprompted(paths, tmp_path):
    config, tools = _setup(paths, tmp_path)
    await _install(tools["install_skill"], AnsweringAsker(ALLOW_ONCE), tmp_path)
    asker = AnsweringAsker(DENY)
    await _run(tools["remove_skill"], {"name": "standalone"}, asker, tmp_path)
    assert asker.questions == []
    assert not _installed(config)
