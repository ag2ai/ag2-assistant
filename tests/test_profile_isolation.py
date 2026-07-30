"""Cross-profile isolation at the live-runtime / gateway level (§6.3, WP7 item 2).

Two live runtimes A and B boot in one process against a tmp root (HOME is isolated
by the autouse conftest fixture). Agents are faked so nothing touches an LLM. Each
sub-test exercises one of §4.8's sneaky-global surfaces and asserts A's state
changed while B's is absent/unchanged — proving isolation is structural, not query
discipline.

Also covers §6.4 concurrency: A's scheduler fires while B is active.
(Restart-after-archive lives in ``test_profile_manager.py`` per §6.6.)
"""

import asyncio
from contextlib import AsyncExitStack

import pytest
from fastapi.testclient import TestClient

from assistant import profiles
from tests.conftest import api, use_fake_agent


async def _run_tool(tool, **kwargs):
    """Execute an AG2 @tool's underlying function the way the agent loop does
    (through its fast_depends CallModel), so isolation tests exercise the real tool
    body — not a hand-rolled re-implementation."""
    async with AsyncExitStack() as stack:
        return await tool.model.asolve(stack=stack, cache_dependencies={}, **kwargs)


def _two_profile_client(monkeypatch):
    """A started app with two live profiles A (work) and B (personal); returns
    ``(client_ctx, )`` — use as ``with _two_profile_client(mp) as client:``."""
    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    use_fake_agent(monkeypatch)
    manager = ProfileManager(memory=False, persist=True)
    app = create_app(manager)
    return TestClient(app)


def _boot_two(client):
    """Create work + personal over HTTP on an already-open client. Returns (a, b)."""
    client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
    client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
    return "work", "personal"


# --- a. chat isolation: A's chats has it, B's is empty; dbs in right dirs ---


def test_chats_isolated(monkeypatch):
    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)

        r = client.post(api(a, "/message"), json={"text": "hi A", "chat_id": "s-a"})
        assert r.status_code == 200
        assert r.json()["reply"].startswith("echo[1]")

        a_chats = client.get(api(a, "/chats")).json()["chats"]
        b_chats = client.get(api(b, "/chats")).json()["chats"]
        assert any(s["chat_id"] == "s-a" for s in a_chats)
        assert b_chats == []  # B never saw the chat

        # each profile has its OWN chats.db under its own dir (persist=True creates
        # one per runtime at boot); the chat lives only in A's, and neither is the root's
        assert (profiles.profile_dir(a) / "chats.db").exists()
        assert (profiles.profile_dir(b) / "chats.db").exists()
        assert profiles.profile_dir(a) != profiles.profile_dir(b)
        assert not (profiles.data_dir() / "chats.db").exists()


# --- b. remember tool: A's profile.db changes, B's absent; GET B/memory empty ---


def test_remember_tool_isolated(monkeypatch):
    """Invoke the built memory tool the way agent.py wires it (build_memory_tool over
    A's profile + universal store paths). A profile-scoped note lands in A's profile.db;
    B's memory endpoint stays empty."""
    from assistant.agent import build_memory_tool

    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)
        a_cfg = client.app.state.profiles.get(a).config
        a_store = a_cfg.data_dir / "profile.db"
        user_store = a_cfg.root_dir / "user.db"

        remember = build_memory_tool(a_store, user_store)
        asyncio.run(
            _run_tool(remember, note="A likes terse answers", category="how", scope="profile")
        )

        assert a_store.exists()  # A's memory db written
        a_mem = client.get(api(a, "/memory")).json()["text"]
        assert "A likes terse answers" in a_mem

        # B is untouched: no profile.db, empty memory endpoint
        assert not (profiles.profile_dir(b) / "profile.db").exists()
        assert client.get(api(b, "/memory")).json()["text"] == ""
        # a profile-scoped note must NOT have leaked into the shared universal layer
        assert client.get("/api/memory").json()["text"] == ""


def test_remember_tool_universal_scope_shared(monkeypatch):
    """A remember(scope="universal") writes the shared root/user.db — readable via the
    GLOBAL /api/memory and injected into BOTH profiles' contexts. A remember(scope=
    "profile") stays in that one profile's profile.db and is NOT visible universally."""
    from assistant.agent import build_memory_tool, universal_memory_guidance

    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)
        a_cfg = client.app.state.profiles.get(a).config
        b_cfg = client.app.state.profiles.get(b).config
        user_store = a_cfg.root_dir / "user.db"

        # A saves an identity fact universally, and a persona note to itself.
        remember_a = build_memory_tool(a_cfg.data_dir / "profile.db", user_store)
        asyncio.run(
            _run_tool(remember_a, note="Name is TestUser", category="how", scope="universal")
        )
        asyncio.run(
            _run_tool(remember_a, note="A prefers bullet points", category="how", scope="profile")
        )

        # Universal note is on the shared store and readable via the GLOBAL route
        assert (user_store).exists()
        assert "Name is TestUser" in client.get("/api/memory").json()["text"]

        # Both profiles inject the universal doc into their per-turn context
        a_ctx = universal_memory_guidance(a_cfg)
        b_ctx = universal_memory_guidance(b_cfg)
        assert "Name is TestUser" in a_ctx
        assert "Name is TestUser" in b_ctx
        assert "shared across all profiles" in a_ctx.lower() or "shared across" in a_ctx

        # The persona note is NOT in the universal layer, nor in B's context
        assert "A prefers bullet points" not in client.get("/api/memory").json()["text"]
        assert "A prefers bullet points" not in b_ctx
        # B's own profile memory is still empty (the persona note stayed in A)
        assert client.get(api(b, "/memory")).json()["text"] == ""


def test_global_memory_api_roundtrip_shared(monkeypatch):
    """POST /api/memory (global) then GET /api/memory returns the same doc — and the
    doc is the SAME whether the caller was 'in' profile A or B (it's install-wide)."""
    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)

        marker = "# User profile\n- Name: TestUser"
        assert client.post("/api/memory", json={"text": marker}).status_code == 200
        assert client.get("/api/memory").json()["text"] == marker

        # per-profile persona memory is a DIFFERENT store — still empty for both
        assert client.get(api(a, "/memory")).json()["text"] == ""
        assert client.get(api(b, "/memory")).json()["text"] == ""


# --- c. settings: A/settings/focuses updates A only; reload keeps A's data_dir ---


def test_settings_focuses_isolated_and_reload_keeps_paths(monkeypatch):
    """POST A/settings/focuses updates A's settings.json and reloads A's runtime; B is
    untouched. (The LLM is install-wide now, so focuses is the per-profile setting that
    exercises the same reload path.) Regression for the load_config() bug: after the
    reload A's runtime config still resolves A's data_dir (paths didn't revert to
    root/B) and reflects the new setting."""
    from assistant.settings import Settings

    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)

        a_runtime = client.app.state.profiles.get(a)
        a_data_dir = a_runtime.config.data_dir

        r = client.post(api(a, "/settings/focuses"), json={"focuses": ["research", "coding"]})
        assert r.status_code == 200, r.text

        # A's settings.json updated; B's has no focuses
        assert Settings(profiles.profile_dir(a) / "config.yaml").get_focuses() == [
            "research",
            "coding",
        ]
        assert Settings(profiles.profile_dir(b) / "config.yaml").get_focuses() == []

        # regression: after reload A's config still points at A's data_dir (not root/B)
        assert a_runtime.config.data_dir == a_data_dir
        assert a_runtime.config.data_dir == profiles.profile_dir(a)

        # B's runtime config untouched
        b_runtime = client.app.state.profiles.get(b)
        assert b_runtime.config.data_dir == profiles.profile_dir(b)


# --- c2. per-profile LLM Active override (ADR 0015): A overrides, B inherits ---


def test_llm_override_isolated_via_endpoint(monkeypatch):
    """POST A/settings/llm-override points A's Active Text model at a shared config and
    reloads A's runtime; B inherits the install-wide Active and the install-wide Active
    itself never moves. Clearing restores inheritance; an unknown id is a 404."""
    from assistant import llm_configs

    monkeypatch.delenv("AG2ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AG2ASSISTANT_MODEL", raising=False)
    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)
        e1 = llm_configs.save_config({"name": "A", "type": "anthropic", "model": "claude-x"})
        e2 = llm_configs.save_config({"name": "O", "type": "openai", "model": "gpt-x"})
        # Activate e1 install-wide via the real endpoint so EVERY runtime reloads and
        # picks up the store (mirrors the composer switch); B then inherits claude-x.
        assert client.post(f"/api/llm-configs/{e1['id']}/use").status_code == 200

        r = client.post(api(a, "/settings/llm-override"), json={"config_id": e2["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["llm_override"] == e2["id"]

        sa = client.get(api(a, "/settings")).json()
        assert sa["llm_override"] == e2["id"]
        assert sa["llm_active"] == e2["id"]
        assert sa["assistant"]["model"] == "gpt-x"  # A's runtime rebuilt on the override

        sb = client.get(api(b, "/settings")).json()
        assert sb["llm_override"] is None  # B never overrode
        assert sb["llm_active"] == e1["id"]
        assert sb["assistant"]["model"] == "claude-x"

        assert llm_configs.active_id() == e1["id"]  # install-wide Active untouched

        # Clear A's override → back to the inherited install-wide Active.
        r = client.post(api(a, "/settings/llm-override"), json={"config_id": ""})
        assert r.status_code == 200 and r.json()["llm_override"] is None
        sa = client.get(api(a, "/settings")).json()
        assert sa["llm_override"] is None
        assert sa["assistant"]["model"] == "claude-x"

        # Unknown id is rejected (never persisted).
        bad = client.post(api(a, "/settings/llm-override"), json={"config_id": "c_ghost"})
        assert bad.status_code == 404


# --- d. usage: recording a turn's usage in A writes A/usage.json only ---


def test_usage_ledger_isolated(monkeypatch):
    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)
        a_gw = client.app.state.profiles.get(a).gateway

        # tally the way a turn does: the gateway's per-profile ledger
        a_gw._usage.record("gemini-3.6-flash", prompt_tokens=100, completion_tokens=50)

        assert (profiles.profile_dir(a) / "usage.json").exists()
        assert not (profiles.profile_dir(b) / "usage.json").exists()
        assert not (profiles.data_dir() / "usage.json").exists()  # not the root

        a_today = client.get(api(a, "/usage")).json()
        assert a_today["total"] == 150
        b_today = client.get(api(b, "/usage")).json()
        assert b_today["total"] == 0


# --- e. skills: a SKILL.md under A's skills_dir; B's lacks it; dirs differ ---


def test_skills_dir_isolated(monkeypatch):
    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)
        a_cfg = client.app.state.profiles.get(a).config
        b_cfg = client.app.state.profiles.get(b).config

        # each profile's skills_dir is its own profile-dir subfolder, never shared
        assert a_cfg.skills_dir == profiles.profile_dir(a) / "skills"
        assert b_cfg.skills_dir == profiles.profile_dir(b) / "skills"
        assert a_cfg.skills_dir != b_cfg.skills_dir

        # "install" a skill into A via config.skills_dir
        a_cfg.skills_dir.mkdir(parents=True, exist_ok=True)
        (a_cfg.skills_dir / "SKILL.md").write_text("# a-only skill")

        assert (a_cfg.skills_dir / "SKILL.md").exists()
        assert not (b_cfg.skills_dir / "SKILL.md").exists()


# --- e2. skills: a Suppression in A is invisible to B; install-wide Disable hits both ---


def test_skill_suppression_isolated_but_disable_is_global(monkeypatch):
    """A per-profile Suppression (ADR 0016 t02) turns a shared skill off for A only —
    B's resolved skill set is unchanged. An install-wide Disable, by contrast, changes
    both. Asserted through the /api/p/{pid}/skills projection the Skills tab reads."""
    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)

        def avail(pid, name):
            rows = {s["name"]: s for s in client.get(api(pid, "/skills")).json()["skills"]}
            return rows[name]["available"]

        # Baseline: both profiles see the shared bundled skill.
        assert avail(a, "web-research") is True
        assert avail(b, "web-research") is True

        # Suppress in A only → A loses it, B keeps it.
        assert client.post(api(a, "/skills/web-research/suppress")).status_code == 200
        assert avail(a, "web-research") is False
        assert avail(b, "web-research") is True  # B's resolved set is untouched

        # An install-wide Disable of a DIFFERENT skill changes BOTH profiles.
        assert (
            client.post("/api/skills/pdf-tools/state", json={"enabled": False}).status_code == 200
        )
        assert avail(a, "pdf-tools") is False
        assert avail(b, "pdf-tools") is False


# --- f. permissions: now GLOBAL — a grant is install-wide, visible to every profile ---


def test_permissions_are_global(monkeypatch):
    """Permissions moved from per-profile to a single install-wide store at
    config.root_dir/permissions.json — a grant made against one runtime's store is
    visible to the other, and no per-profile permissions.json is ever created."""
    from assistant.permissions import PermissionStore

    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)
        a_cfg = client.app.state.profiles.get(a).config
        b_cfg = client.app.state.profiles.get(b).config

        # both runtimes point at the SAME file, under the shared root (not a profile dir)
        root_perm = a_cfg.root_dir / "permissions.json"
        assert b_cfg.root_dir / "permissions.json" == root_perm
        assert a_cfg.data_dir != b_cfg.data_dir  # profiles are still isolated elsewhere

        PermissionStore(root_perm).grant_command("gmail_send")

        # visible via BOTH profiles' gateway stores (they share the file)
        assert (
            "gmail_send" in client.app.state.profiles.get(a).gateway._permissions.granted_commands()
        )
        assert (
            "gmail_send" in client.app.state.profiles.get(b).gateway._permissions.granted_commands()
        )
        # and no per-profile file was created
        assert not (profiles.profile_dir(a) / "permissions.json").exists()
        assert not (profiles.profile_dir(b) / "permissions.json").exists()


# --- g. MCP: POST A/settings/mcp; A lists it, B doesn't; A's agent tools include it ---


def test_mcp_server_isolated(monkeypatch):
    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)

        server = {"name": "echo-mcp", "command": "echo", "args": ["hi"], "enabled": True}
        r = client.post(api(a, "/settings/mcp"), json=server)
        assert r.status_code == 200, r.text

        a_names = [s["name"] for s in client.get(api(a, "/settings")).json()["mcp_servers"]]
        b_names = [s["name"] for s in client.get(api(b, "/settings")).json()["mcp_servers"]]
        assert "echo-mcp" in a_names
        assert "echo-mcp" not in b_names

        # exercise the tools/__init__.py per-profile settings path: A's agent tool
        # build (mcp capability) includes the server's toolkit, B's does not. Building
        # the toolkit object does not launch the stdio process.
        from assistant.tools import build_agent_tools

        a_cfg = client.app.state.profiles.get(a).config
        b_cfg = client.app.state.profiles.get(b).config
        a_tools = build_agent_tools(capabilities=["mcp"], config=a_cfg)
        b_tools = build_agent_tools(capabilities=["mcp"], config=b_cfg)
        assert "echo-mcp" in [t.name for t in a_tools], [t.name for t in a_tools]
        assert "echo-mcp" not in [t.name for t in b_tools]


# --- h. voice system tools: set_voice in A changes A's settings only ---


def test_voice_system_tool_isolated(monkeypatch):
    """Use the voice get/set system tools built for A (build_system_tools with A's
    Settings) — setting a voice writes A's settings.json, not B's (§4.8 system_tools row)."""
    from assistant import voice_providers
    from assistant.settings import Settings
    from assistant.system_tools import build_system_tools

    with _two_profile_client(monkeypatch) as client:
        a, b = _boot_two(client)
        a_runtime = client.app.state.profiles.get(a)
        a_settings = Settings(a_runtime.config.data_dir / "config.yaml")

        # pick a valid non-default voice for the active provider
        provider = voice_providers.get(a_settings.voice_provider())
        target = next(v for v in provider.voices if v != provider.default_voice)

        tools = build_system_tools(a_runtime.tasks, a_settings, chats=a_runtime.gateway)
        set_voice = next(t for t in tools if t.name == "set_voice")
        msg = asyncio.run(_run_tool(set_voice, voice=target))
        assert "Voice set to" in msg

        assert Settings(profiles.profile_dir(a) / "config.yaml").get_voice() == target
        # B's settings.json has no voice override → its provider default
        b_settings = Settings(profiles.profile_dir(b) / "config.yaml")
        assert (
            b_settings.get_voice() == voice_providers.get(b_settings.voice_provider()).default_voice
        )


# --- §6.4 concurrency: A's scheduler fires while B is active ---


async def test_a_scheduler_fires_while_b_active(monkeypatch):
    """Schedule a near-due task in A's runtime, interact with B, and assert A's
    scheduler autonomously fires A's task to a terminal state — deterministic (fake
    agent, short interval, poll with timeout; no long sleeps)."""
    from datetime import datetime, timedelta

    from assistant.config import load_config
    from assistant.gateway.core import build_gateway
    from assistant.tasks.model import RunStatus

    use_fake_agent(monkeypatch)

    a_meta = profiles.create_profile("Work", "#109e91")
    b_meta = profiles.create_profile("Personal", "#f95339")
    profiles.profile_dir(a_meta.id).mkdir(parents=True, exist_ok=True)
    profiles.profile_dir(b_meta.id).mkdir(parents=True, exist_ok=True)

    a_cfg = load_config().with_profile(a_meta)
    b_cfg = load_config().with_profile(b_meta)
    a_gw, a_tasks = build_gateway(a_cfg, memory=False, persist=True)
    b_gw, b_tasks = build_gateway(b_cfg, memory=False, persist=True)
    # build_gateway wires the TaskService into the Gateway's agent tools, but the
    # reverse wiring (turns/stops for runs) is the caller's job — normally done by
    # ProfileManager; do it here since this test drives build_gateway directly.
    a_tasks.set_gateway(a_gw)
    b_tasks.set_gateway(b_gw)
    # A ticks fast so the test stays deterministic without long sleeps
    a_tasks._scheduler_interval = 0.05
    await a_gw.start()
    await b_gw.start()
    await a_tasks.start()
    await b_tasks.start()

    try:
        # seed a near-due one-off task directly on A's store (bypass the tool
        # layer/LLM); the fake agent (echo) stands in for the run's turn.
        now = datetime.now().astimezone()
        task = await a_tasks.store.create_task(
            "due soon",
            "produce the output",
            schedule={"kind": "once", "at": (now - timedelta(seconds=1)).isoformat()},
        )

        # meanwhile, drive a message through B (B is the "active" profile)
        reply = await b_gw.send_message("busy over here", chat_id="b1")
        assert reply.startswith("echo[")

        # A's scheduler fired A's task autonomously while B was in use — poll for
        # its run to land in a terminal state (no long sleeps: short interval +
        # bounded poll).
        async def _terminal_run():
            for _ in range(100):
                t = await a_tasks.get_task(task.id)
                last = t["last_run"] if t else None
                if last and last["status"] in RunStatus.TERMINAL:
                    return last
                await asyncio.sleep(0.05)
            return None

        run = await asyncio.wait_for(_terminal_run(), timeout=5)
        assert run is not None and run["status"] == "completed"
        # B's store never saw the task
        assert await b_tasks.store.get_task(task.id) is None
    finally:
        await a_tasks.close()
        await b_tasks.close()
        await a_gw.close()
        await b_gw.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
