"""A chat turn's capability set and memory switch, and the product name in its identity line."""

import hashlib
import json

import pytest
from ag2.events import ToolCallEvent, ToolNotFoundEvent, ToolResultEvent
from fastapi.testclient import TestClient

from assistant.agent import SKILL_USAGE_GUIDANCE, universal_turn_prompt
from assistant.config import Config
from assistant.gateway.app import create_app
from assistant.memory import PROFILE_PATH, read_profile_sync, write_universal
from assistant.profiles import ProfileRegistry
from assistant.settings import profile_settings
from assistant.yamlio import write_yaml
from tests.support.apps import api, make_manager
from tests.support.chat import run, run_input
from tests.support.fakes import ScriptedModels

WHO = "Who the user is (shared across all profiles)"
IDENTITY = (
    "You are AG2 Assistant — ONE assistant the user works with everywhere: a web chat, a "
    "task's own page, and messaging channels (Telegram, etc.). It must always feel "
    "like the same entity. You can:\n- Chat and use your tools"
)
NO_TOOLS = {"capabilities": [], "memory": False}
SKILLS_PLUGIN = SKILL_USAGE_GUIDANCE.splitlines()[0]


def _app(paths, models, *, memory=False, env=None):
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    paths.profile_dir(pid).mkdir(parents=True, exist_ok=True)
    manager = make_manager(paths, env=env, persist=True, memory=memory, model_factory=models)
    return create_app(manager), pid


def _turns(models):
    """The turn model's requests: (tool names, prompt), without background merges. A
    provider builtin has no function, so it goes by its schema's type."""
    out = []
    for _, prompt, schemas, _, selected in models.requests:
        if selected is None:
            names = {
                s.function.name if hasattr(s, "function") else type(s).__name__ for s in schemas
            }
            out.append((names, "\n".join(prompt)))
    return out


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _settle(client, app, pid):
    """Await every background job of the profile's gateway, the memory update among them."""
    gateway = app.state.profiles.get(pid).require_gateway()

    async def settle():
        while gateway._background:
            for task in list(gateway._background):
                await task

    client.portal.call(settle)


def test_a_configured_product_name_replaces_ag2_assistant_in_the_identity_line(paths):
    config = Config.for_paths(paths)
    config.agent.product_name = "Space Assistant"
    prompt = "\n".join(universal_turn_prompt(config, google=False))
    assert IDENTITY.replace("AG2 Assistant", "Space Assistant") in prompt
    assert "AG2 Assistant" not in prompt


def test_without_a_product_name_the_identity_line_is_unchanged(paths):
    assert IDENTITY in "\n".join(universal_turn_prompt(Config.for_paths(paths), google=False))


def test_a_deployment_sets_the_product_name_every_turn_carries(paths):
    write_yaml(paths.config_yaml, {"agent": {"product_name": "Space Assistant"}})
    models = ScriptedModels()
    app, pid = _app(paths, models)
    with TestClient(app) as client:
        run(client, pid, text="hello")
        run(client, pid, text="hello", chat="guest-1", run_id="r2", props=NO_TOOLS)
    for _, prompt in _turns(models):
        assert IDENTITY.replace("AG2 Assistant", "Space Assistant").split(" You can:")[0] in prompt
        assert "You are AG2 Assistant —" not in prompt


def test_a_turn_with_an_empty_capability_set_gets_no_tools_and_no_skills_plugin(paths):
    config = {"llm": {"config_type": "anthropic", "builtin_tools": {"web_fetch": {}}}}
    write_yaml(paths.config_yaml, config)
    models = ScriptedModels()
    app, pid = _app(paths, models)
    with TestClient(app) as client:
        run(client, pid, text="hello")
        run(client, pid, text="hello", chat="guest-1", run_id="r2", props=NO_TOOLS)
    (everything, full), (scoped, prompt) = _turns(models)
    assert {"WebFetchToolSchema", "load_skill", "install_skill", "list_tasks"} <= everything
    assert {"describe_settings", "run_skill_script", "fetch_page", "read_file"} <= everything
    assert SKILLS_PLUGIN in full
    assert scoped == {"read_compaction_source"}
    assert SKILLS_PLUGIN not in prompt
    assert "list_tasks" not in prompt and "write_file" not in prompt


def test_a_turn_with_an_empty_capability_set_cannot_install_or_run_a_skill(paths):
    skills = paths.skills_dir
    models = ScriptedModels(
        script=lambda config, model: [
            ToolCallEvent(name="install_skill", arguments=json.dumps({"name": "x"})),
            ToolCallEvent(
                name="run_skill_script",
                arguments=json.dumps({"name": "x", "script": "scripts/run.py"}),
            ),
            "done",
        ]
    )
    app, pid = _app(paths, models)
    with TestClient(app) as client:
        before = sorted(p.name for p in skills.iterdir()) if skills.exists() else []
        run(client, pid, text="install a skill", chat="guest-1", props=NO_TOOLS)
        gateway = app.state.profiles.get(pid).require_gateway()
        events = client.portal.call(gateway.replay_events, "guest-1")
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert {e.name for e in results} == {"install_skill", "run_skill_script"}
    assert all(isinstance(e, ToolNotFoundEvent) for e in results)
    assert (sorted(p.name for p in skills.iterdir()) if skills.exists() else []) == before


def test_a_turn_keeps_only_the_groups_in_its_set(paths):
    models = ScriptedModels()
    app, pid = _app(paths, models)
    with TestClient(app) as client:
        run(client, pid, text="hello", props={"capabilities": ["web"]})
    [(names, _)] = _turns(models)
    assert {"duckduckgo_search", "web_fetch"} <= names
    assert not names & {"load_skill", "install_skill", "list_tasks", "describe_settings"}
    assert not names & {"read_file", "write_file", "run_shell_command", "run_code", "ask_user"}


def test_a_turn_without_memory_neither_reads_nor_updates_the_profile(paths):
    def script(config, model):
        return ["GUEST-LEARNED"] if model else ["reply"]

    models = ScriptedModels(script=script)
    app, pid = _app(paths, models, memory=True, env={"AG2ASSISTANT_AGGREGATE_EVERY_N": "1"})
    profile_db = paths.profile_dir(pid) / "profile.db"
    user_db = paths.root / "user.db"
    with TestClient(app) as client:
        gateway = app.state.profiles.get(pid).require_gateway()
        client.portal.call(gateway._knowledge_store.write, PROFILE_PATH, "OWNER-PROFILE")
        client.portal.call(write_universal, "- OWNER-IDENTITY", user_db)
        stored, shared = _digest(profile_db), _digest(user_db)
        for n in range(4):
            run(client, pid, text=f"guest ask {n}", chat="guest-a", run_id=f"a{n}", props=NO_TOOLS)
            _settle(client, app, pid)
        run(client, pid, text="what do you know?", chat="guest-b", run_id="b", props=NO_TOOLS)
        _settle(client, app, pid)
        assert client.portal.call(gateway._knowledge_store.read, PROFILE_PATH) == "OWNER-PROFILE"
    assert [model for *_, model in models.requests if model] == []
    assert _digest(profile_db) == stored and _digest(user_db) == shared
    assert read_profile_sync(profile_db) == "OWNER-PROFILE"
    for _, prompt in _turns(models):
        assert "OWNER-PROFILE" not in prompt and "GUEST-LEARNED" not in prompt
        assert "OWNER-IDENTITY" not in prompt and WHO not in prompt


def _merges(models):
    """The profile merges sent to the cheap model, prompt and conversation as text."""
    out = []
    for _, prompt, _, messages, selected in models.requests:
        parts = [part for m in messages for part in getattr(m, "parts", ())]
        text = "\n".join([*prompt, *(str(getattr(p, "content", "")) for p in parts)])
        if selected is not None and "Update the profile below" in text:
            out.append(text)
    return out


def test_a_chat_that_ran_without_memory_never_feeds_the_profile(paths):
    """A later turn WITH memory on the same chat, before and after a restart, merges
    none of it: the merge works per chat, so the whole chat stays out."""
    models = ScriptedModels(script=lambda config, model: ["LEARNED"] if model else ["reply"])
    env = {"AG2ASSISTANT_AGGREGATE_EVERY_N": "1"}
    app, pid = _app(paths, models, memory=True, env=env)
    with TestClient(app) as client:
        client.portal.call(write_universal, "- OWNER-IDENTITY", paths.root / "user.db")
        run(client, pid, text="owner hello", chat="c", run_id="r1")
        _settle(client, app, pid)
        run(client, pid, text="GUEST-SECRET-42", chat="c", run_id="r2", props=NO_TOOLS)
        _settle(client, app, pid)
        run(client, pid, text="owner again", chat="c", run_id="r3")
        _settle(client, app, pid)
    manager = make_manager(paths, env=env, persist=True, memory=True, model_factory=models)
    app = create_app(manager)
    with TestClient(app) as client:
        run(client, pid, text="owner after restart", chat="c", run_id="r4")
        _settle(client, app, pid)
        run(client, pid, text="another chat", chat="d", run_id="r5")
        _settle(client, app, pid)
    merges = _merges(models)
    assert any("owner hello" in m for m in merges)
    assert any("another chat" in m for m in merges)
    assert not [m for m in merges if "GUEST-SECRET-42" in m]
    assert not [m for m in merges if "owner again" in m or "owner after restart" in m]


def test_turns_without_memory_stay_out_of_the_default_merge(paths):
    """With the default cadence the fourth turn merges the last four, the three before
    it run without memory among them."""
    models = ScriptedModels(script=lambda config, model: ["LEARNED"] if model else ["reply"])
    app, pid = _app(paths, models, memory=True)
    with TestClient(app) as client:
        client.portal.call(write_universal, "- OWNER-IDENTITY", paths.root / "user.db")
        for n in range(3):
            run(client, pid, text=f"GUEST-SECRET-{n}", chat="c", run_id=f"g{n}", props=NO_TOOLS)
            _settle(client, app, pid)
        run(client, pid, text="owner", chat="c", run_id="o")
        _settle(client, app, pid)
    assert not [m for m in _merges(models) if "GUEST-SECRET" in m]


@pytest.mark.parametrize("shared", ["read_write", "read"])
def test_a_turn_without_memory_has_no_who_the_user_is_section(paths, shared):
    models = ScriptedModels()
    app, pid = _app(paths, models, memory=True)
    profile_settings(paths.profile_dir(pid)).set_shared_memory(shared)
    with TestClient(app) as client:
        client.portal.call(write_universal, "- MARKER-7f3", paths.root / "user.db")
        run(client, pid, text="hello")
        run(client, pid, text="hello", chat="guest-1", run_id="r2", props={"memory": False})
    (_, owner), (names, guest) = _turns(models)
    assert WHO in owner and "MARKER-7f3" in owner
    assert WHO not in guest and "MARKER-7f3" not in guest
    assert "remember" not in names


def test_a_turn_without_memory_never_starts_the_first_run_interview(paths):
    models = ScriptedModels()
    app, pid = _app(paths, models, memory=True)
    user_db = paths.root / "user.db"
    with TestClient(app) as client:
        before = _digest(user_db)
        events = run(client, pid, text="hello", chat="guest-1", props={"memory": False})
    assert events[-1]["outcome"]["type"] == "success"
    assert _digest(user_db) == before
    assert len(_turns(models)) == 1


def test_a_turn_without_a_set_or_a_memory_switch_is_unchanged(paths):
    models = ScriptedModels()
    app, pid = _app(paths, models, memory=True)
    with TestClient(app) as client:
        gateway = app.state.profiles.get(pid).require_gateway()
        client.portal.call(gateway._knowledge_store.write, PROFILE_PATH, "OWNER-PROFILE")
        client.portal.call(write_universal, "- MARKER-7f3", paths.root / "user.db")
        run(client, pid, text="hello")
    [(names, prompt)] = _turns(models)
    assert {"remember", "load_skill", "install_skill", "list_tasks", "describe_settings"} <= names
    assert {"web_fetch", "read_file", "ask_user"} <= names
    assert WHO in prompt and "MARKER-7f3" in prompt and "OWNER-PROFILE" in prompt
    assert IDENTITY in prompt and "write_file" in prompt and SKILLS_PLUGIN in prompt


@pytest.mark.parametrize(
    "props",
    [
        {"capabilities": "web"},
        {"capabilities": ["web", 3]},
        {"capabilities": ["the-internet"]},
        {"memory": "false"},
        {"memory": None},
    ],
)
def test_a_malformed_scope_is_refused(paths, props):
    models = ScriptedModels()
    app, pid = _app(paths, models)
    with TestClient(app) as client:
        response = client.post(
            api(pid, "/chats/c1/agui"), json=run_input(text="hello", props=props)
        )
    assert response.status_code == 422, response.text
    assert models.requests == []
