"""Tests for the self-knowledge tools + the bundled skill that routes to them."""

import pytest

from assistant.agent import bundled_skills_dir
from assistant.config import Config
from assistant.folders import READ, READ_WRITE, FolderStore
from assistant.permissions import PermissionManager
from assistant.self_tools import build_self_tools
from assistant.settings import profile_settings
from tests.support.apps import make_paths


class _Ctx:
    """Stand-in for AG2's Context: the tools only read `dependencies`."""

    def __init__(self, deps):
        self.dependencies = deps


def _tools(tmp_path):
    config = Config.for_paths(make_paths(tmp_path))
    config.data_dir = tmp_path / "profiles" / "default"
    config.data_dir.mkdir(parents=True)
    return {t.name: t for t in build_self_tools(config, profile_settings(config.data_dir))}


def _manager(tmp_path, *, profile="default", chat_id=""):
    return PermissionManager(
        folders=FolderStore(tmp_path / "folders.json"),
        profile=profile,
        chat_id=chat_id,
    )


async def _run(tool, **kwargs):
    """Execute an AG2 @tool through its CallModel, the way the agent loop does."""
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        return await tool.model.asolve(stack=stack, cache_dependencies={}, **kwargs)


@pytest.mark.asyncio
async def test_no_grants_says_allowlist_not_blocked(tmp_path):
    """The common case: nothing is reachable, and nothing is 'blocked' either."""
    tools = _tools(tmp_path)
    out = await _run(tools["list_folders"], context=_Ctx({PermissionManager: _manager(tmp_path)}))
    assert "Nothing is blocked" in out
    assert "Settings → Folders" in out


@pytest.mark.asyncio
async def test_profile_grant_is_reported(tmp_path):
    target = tmp_path / "docs"
    target.mkdir()
    mgr = _manager(tmp_path)
    folder = mgr.folders.create_folder(target, name="Docs")
    mgr.folders.set_grant(folder["id"], READ, profile="default")

    out = await _run(_tools(tmp_path)["list_folders"], context=_Ctx({PermissionManager: mgr}))
    assert "Docs" in out
    assert "read only" in out


@pytest.mark.asyncio
async def test_chat_grant_is_visible_only_in_that_chat(tmp_path):
    """The whole reason this tool reads the turn's PermissionManager: a chat-scoped
    Grant must show up in its own chat and nowhere else."""
    target = tmp_path / "shared"
    target.mkdir()
    store = FolderStore(tmp_path / "folders.json")
    folder = store.create_folder(target, name="Shared")
    store.set_grant(folder["id"], READ_WRITE, profile="default", chat_id="chat-1")

    tools = _tools(tmp_path)
    in_chat = await _run(
        tools["list_folders"],
        context=_Ctx({PermissionManager: _manager(tmp_path, chat_id="chat-1")}),
    )
    assert "Shared" in in_chat
    assert "read + write" in in_chat

    other_chat = await _run(
        tools["list_folders"],
        context=_Ctx({PermissionManager: _manager(tmp_path, chat_id="chat-2")}),
    )
    assert "Shared" not in other_chat
    assert "no Grant for this" in other_chat


@pytest.mark.asyncio
async def test_grant_is_persona_scoped(tmp_path):
    """A Grant carries a profile even when chat-scoped — another persona sees nothing."""
    target = tmp_path / "mine"
    target.mkdir()
    store = FolderStore(tmp_path / "folders.json")
    folder = store.create_folder(target, name="Mine")
    store.set_grant(folder["id"], READ, profile="default")

    out = await _run(
        _tools(tmp_path)["list_folders"],
        context=_Ctx({PermissionManager: _manager(tmp_path, profile="other")}),
    )
    assert "Mine" not in out


@pytest.mark.asyncio
async def test_allow_once_is_not_reported_as_access(tmp_path):
    """Turn-scoped `_once` is not a Grant; reporting it as access would be a lie."""
    target = tmp_path / "temp"
    target.mkdir()
    mgr = _manager(tmp_path)
    mgr.folders.create_folder(target, name="Temp")
    mgr._once[str(target.resolve())] = True

    out = await _run(_tools(tmp_path)["list_folders"], context=_Ctx({PermissionManager: mgr}))
    assert "Temp" not in out


@pytest.mark.asyncio
async def test_missing_directory_is_repointable_not_an_error(tmp_path):
    target = tmp_path / "gone"
    target.mkdir()
    mgr = _manager(tmp_path)
    folder = mgr.folders.create_folder(target, name="Gone")
    mgr.folders.set_grant(folder["id"], READ, profile="default")
    target.rmdir()

    out = await _run(_tools(tmp_path)["list_folders"], context=_Ctx({PermissionManager: mgr}))
    assert "repointed" in out


@pytest.mark.asyncio
async def test_describe_settings_states_model_is_install_wide(tmp_path):
    out = await _run(_tools(tmp_path)["describe_settings"])
    assert "install-wide" in out


@pytest.mark.asyncio
async def test_describe_integrations_reports_google_and_mcp(tmp_path):
    out = await _run(_tools(tmp_path)["describe_integrations"])
    assert "Google:" in out
    assert "MCP servers" in out


def test_self_knowledge_skill_is_bundled():
    skill = bundled_skills_dir() / "self-knowledge" / "SKILL.md"
    assert skill.exists()


_SELF_TOOLS = {"list_folders", "describe_integrations", "describe_settings"}


def test_self_tools_are_wired_into_the_chat_agent(tmp_path):
    """The wiring, not the logic: a chat agent must actually carry these tools.
    Everything else here passes even if create_agent never attaches them."""
    from assistant.agent import create_agent

    config = Config.for_paths(make_paths(tmp_path))
    config.skills_dir = tmp_path / "skills"
    agent = create_agent(config, memory=False, skills=True)
    assert _SELF_TOOLS <= {t.name for t in agent.tools}


def test_scoped_task_subagent_does_not_get_self_tools(tmp_path):
    """Chat only, like ask_user — a scoped subagent answers to its task."""
    from assistant.agent import create_agent

    config = Config.for_paths(make_paths(tmp_path))
    config.skills_dir = tmp_path / "skills"
    agent = create_agent(config, memory=False, skills=True, capabilities=["web"])
    assert not (_SELF_TOOLS & {t.name for t in agent.tools})


def test_skill_reaches_the_available_skills_catalog(tmp_path):
    """The skill is only useful if SkillPlugin discloses it in the system prompt."""
    from assistant.agent import build_skills_plugin, build_skills_runtime

    config = Config.for_paths(make_paths(tmp_path))
    config.skills_dir = tmp_path / "skills"
    plugin = build_skills_plugin(config, build_skills_runtime(config))
    assert "self-knowledge" in "\n".join(plugin._system_prompt)


def test_skill_settings_table_matches_the_real_nav():
    """Anti-drift: the skill hand-writes a where-to-go map, so it can outlive the UI
    it describes. Settings.svelte's PAGES list is the source of truth for the nav —
    if a page is renamed, added, or removed, fail here rather than let the assistant
    confidently misdirect the user. Since the UI was localized (ADR 0031) the labels
    are message KEYS, so the English catalog is the other half of the lookup: a key
    deleted from it fails here too, rather than resolving to nothing."""
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    nav = root / "web" / "src" / "components" / "Settings.svelte"
    catalog_path = root / "web" / "messages" / "en.json"
    if not nav.is_file() or not catalog_path.is_file():
        pytest.skip("web sources not present")
    keys = re.findall(r"label: m\.(\w+)\(\)", nav.read_text())
    catalog = json.loads(catalog_path.read_text())
    unknown = [k for k in keys if k not in catalog]
    assert not unknown, f"Settings.svelte names labels missing from en.json: {unknown}"
    real = {catalog[k] for k in keys}

    text = (bundled_skills_dir() / "self-knowledge" / "SKILL.md").read_text()
    table = text.split("**Settings** has exactly these pages:")[1].split("**Memory is not")[0]
    named = {m.strip() for m in re.findall(r"^\| ([A-Z][A-Za-z &]+) \|", table, re.M)}
    named.discard("Page")

    assert real, "could not parse Settings.svelte's PAGES list"
    assert named == real, f"skill's Settings table has drifted from the real nav: {named ^ real}"


def test_skill_does_not_send_users_to_a_memory_settings_page():
    """There is no Memory page — it's a panel behind Settings → Advanced. The shipped
    MEMORY_GUIDANCE still says 'Settings → Memory'; the skill must not repeat it."""
    text = (bundled_skills_dir() / "self-knowledge" / "SKILL.md").read_text()
    assert "Settings → Memory" not in text.split("**Memory is not")[0]
