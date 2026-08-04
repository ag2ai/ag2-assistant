"""The Chat override: which shared Text model one Chat runs on (ADR 0025).

Resolution is asserted through the reply itself — the fake agent answers with the model
its config was built from, so a turn's reply names the model it actually ran on.
"""

import asyncio

import pytest

from assistant.agent import cheap_model
from assistant.channels.base import InboundMessage
from assistant.channels.router import AvailableProfile, ChannelRouter, Reply
from assistant.config import Config, apply_env_overrides, resolve_config
from assistant.gateway.core import Gateway
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.llm_configs import LlmConfigStore
from assistant.pairing import PairingStore
from assistant.peers import PeerStore
from assistant.profiles import ProfileRegistry
from assistant.settings import profile_settings
from assistant.tasks.store import TaskStore
from tests.support.apps import api
from tests.support.fakes import (
    FakeReply,
    FakeRunMixin,
    fake_summary_factory,
    fake_title_factory,
    model_naming_agent_factory,
)


def _models(paths, *names) -> list[str]:
    """Save one shared Text model per name and make the FIRST one install-wide Active.
    Returns their config ids."""
    store = LlmConfigStore(paths)
    ids = [
        store.save_config({"name": name.upper(), "type": "gemini", "model": name})["id"]
        for name in names
    ]
    store.set_active(ids[0])
    return ids


def _gateway(paths, *, env=None, agent_factory=None, **kwargs) -> Gateway:
    """A gateway on an isolated layout whose config resolves the same way production's
    does (install-wide Active + env), and whose agent never reaches an LLM."""
    env = env or {}
    return Gateway(
        config=resolve_config(env, paths),
        memory=False,
        agent_factory=agent_factory or model_naming_agent_factory(),
        config_factory=lambda: resolve_config(env, paths),
        **kwargs,
    )


def _profile_gateway(paths, *, override_id="", agent_factory=None, **kwargs) -> Gateway:
    """A gateway on a real Profile, whose config is built by ``with_profile`` exactly as
    production's is — so the profile's own Active override is part of the chain."""
    meta = ProfileRegistry(paths).create_profile("P", "#109e91")
    paths.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    if override_id:
        profile_settings(paths.profile_dir(meta.id)).set_llm_override(override_id)

    def build() -> Config:
        return resolve_config({}, paths).with_profile(meta, env={})

    return Gateway(
        config=build(),
        memory=False,
        agent_factory=agent_factory or model_naming_agent_factory(),
        config_factory=build,
        **kwargs,
    )


@pytest.fixture
async def gw(paths):
    _models(paths, "model-a", "model-b")
    gw = _gateway(paths)
    await gw.start()
    yield gw
    await gw.close()


def _ids(paths) -> tuple[str, str]:
    store = LlmConfigStore(paths)
    by_model = {c["model"]: c["id"] for c in store.list_configs()}
    return by_model["model-a"], by_model["model-b"]


# --- Resolution (gateway seam) ---


async def test_a_chat_with_no_override_follows_the_active_model(paths, gw):
    """No override = inherit: the chat runs on the Active model, and moves with it."""
    assert await gw.send_message("hi", chat_id="c1") == "model-a"

    _a, b = _ids(paths)
    LlmConfigStore(paths).set_active(b)
    await gw.reload()
    assert await gw.send_message("hi", chat_id="c1") == "model-b"


async def test_an_override_runs_the_next_message_on_that_model(paths, gw):
    """Setting one moves that chat only — other chats and the install-wide Active
    are untouched."""
    a, b = _ids(paths)
    await gw.send_message("hi", chat_id="c1")
    assert await gw.send_message("hi", chat_id="c2") == "model-a"

    assert await gw.update_chat("c1", model=b) is True
    assert await gw.send_message("hi", chat_id="c1") == "model-b"
    assert await gw.send_message("hi", chat_id="c2") == "model-a"
    assert LlmConfigStore(paths).active_id() == a


async def test_clearing_with_the_empty_string_returns_to_inheriting(paths, gw):
    a, b = _ids(paths)
    await gw.send_message("hi", chat_id="c1")
    await gw.update_chat("c1", model=b)
    assert await gw.send_message("hi", chat_id="c1") == "model-b"

    assert await gw.update_chat("c1", model="") is True
    assert await gw.chat_model("c1") == ""
    assert await gw.send_message("hi", chat_id="c1") == "model-a"
    assert LlmConfigStore(paths).active_id() == a


async def test_the_override_survives_a_gateway_restart(paths, gw):
    _a, b = _ids(paths)
    await gw.send_message("hi", chat_id="c1")
    await gw.update_chat("c1", model=b)
    await gw.close()

    restarted = _gateway(paths)
    await restarted.start()
    try:
        assert await restarted.chat_model("c1") == b
        assert await restarted.send_message("hi", chat_id="c1") == "model-b"
    finally:
        await restarted.close()


async def test_an_override_naming_a_deleted_model_falls_back_silently(paths, gw):
    """A dangling override degrades to the Active model, and reads as no override at
    all — so every surface renders the model the turn actually ran on."""
    await gw.send_message("hi", chat_id="c1")
    await gw.update_chat("c1", model="c_deleted")

    assert await gw.send_message("hi", chat_id="c1") == "model-a"
    assert await gw.chat_model("c1") == ""
    # …and what /status and the WebUI render agrees with what the turn ran on.
    assert await gw.effective_model("c1") == _ids(paths)[0]


async def test_an_override_on_a_model_that_cannot_run_fails_the_turn(paths):
    """No rescue path: an existing-but-unusable model fails the turn, exactly as an
    unusable install-wide Active does."""
    _models(paths, "model-a", "model-b")
    gw = _gateway(paths, agent_factory=model_naming_agent_factory(unusable={"model-b"}))
    await gw.start()
    try:
        _a, b = _ids(paths)
        await gw.send_message("hi", chat_id="c1")
        await gw.update_chat("c1", model=b)
        with pytest.raises(RuntimeError, match="cannot run"):
            await gw.send_message("hi", chat_id="c1")
    finally:
        await gw.close()


async def test_renaming_or_starring_leaves_the_override_alone(paths, gw):
    _a, b = _ids(paths)
    await gw.send_message("hi", chat_id="c1")
    await gw.update_chat("c1", model=b)
    await gw.update_chat("c1", title="Renamed")
    await gw.update_chat("c1", starred=True)
    assert await gw.chat_model("c1") == b

    await gw.update_chat("c1", model="")  # and the reverse: clearing keeps the rest
    listed = next(c for c in await gw.list_chats() if c["chat_id"] == "c1")
    assert listed["title"] == "Renamed" and listed["starred"] is True


async def test_a_chat_with_no_override_follows_the_profiles_active_override(paths):
    """The profile's own Active override is the layer under the Chat's: an
    un-overridden chat runs on it, not on the install-wide Active."""
    _models(paths, "model-a", "model-b")
    _a, b = _ids(paths)
    gw = _profile_gateway(paths, override_id=b)
    await gw.start()
    try:
        assert await gw.send_message("hi", chat_id="c1") == "model-b"
        assert LlmConfigStore(paths).active_id() == _a
    finally:
        await gw.close()


async def test_a_chat_override_beats_the_profiles_active_override(paths):
    _models(paths, "model-a", "model-b", "model-c")
    _a, b, c = (cfg["id"] for cfg in LlmConfigStore(paths).list_configs())
    gw = _profile_gateway(paths, override_id=b)
    await gw.start()
    try:
        assert await gw.send_message("hi", chat_id="c1") == "model-b"
        await gw.update_chat("c1", model=c)
        assert await gw.send_message("hi", chat_id="c1") == "model-c"
        assert await gw.send_message("hi", chat_id="c2") == "model-b"  # and only that chat
    finally:
        await gw.close()


class _GatedAgent(FakeRunMixin):
    """Names its model like ``ModelNamingAgent``, but only once released — so a test
    can change the chat while the turn is in flight."""

    def __init__(self, config, started, gate):
        self.config, self.tools = config, []
        self._started, self._gate = started, gate

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        self._started.set()
        await self._gate.wait()
        return FakeReply(self.config.llm.model)


async def _wait_for_title(gw, chat_id: str) -> str:
    """The auto-titler is fire-and-forget — wait for the title it persists."""
    for _ in range(500):
        row = next((c for c in await gw.list_chats() if c["chat_id"] == chat_id), None)
        if row and row.get("title"):
            return row["title"]
        await asyncio.sleep(0.01)
    raise AssertionError("no title was persisted")


async def test_a_generated_title_ignores_the_override(paths):
    """The cheap-model carve-out (ADR 0025): a chat overridden to an expensive model
    is still named by the PROFILE's cheap model. The override lands while the first
    turn is in flight, so the titler that names the chat runs with it in place."""
    _models(paths, "model-a", "model-b")
    _a, b = _ids(paths)
    built: list[Config] = []
    started, gate = asyncio.Event(), asyncio.Event()
    gw = _gateway(
        paths,
        env={"AG2ASSISTANT_AGGREGATE_MODEL": "cheap-one"},
        agent_factory=lambda config, **kw: _GatedAgent(config, started, gate),
        title_factory=fake_title_factory("Named", built=built),
    )
    setter = _gateway(paths)  # a second gateway over the same layout — its own chat locks
    await gw.start()
    await setter.start()
    try:
        turn = asyncio.ensure_future(gw.send_message("hi", chat_id="c1"))
        await started.wait()
        assert await setter.update_chat("c1", model=b) is True
        gate.set()
        await turn

        assert await _wait_for_title(gw, "c1") == "Named"
        assert await gw.chat_model("c1") == b  # the override was in place all along
        assert built and all(cfg.llm.model == "model-a" for cfg in built)
        assert all(cheap_model(cfg) == "cheap-one" for cfg in built)
    finally:
        await gw.close()
        await setter.close()


async def test_an_override_set_mid_turn_does_not_move_the_turn_in_flight(paths):
    """Resolution is settled at the top of the send path: an override that genuinely
    lands while the turn runs governs the NEXT message, never this one."""
    _models(paths, "model-a", "model-b")
    started, gate = asyncio.Event(), asyncio.Event()
    gw = _gateway(paths, agent_factory=lambda config, **kw: _GatedAgent(config, started, gate))
    setter = _gateway(paths)  # a second gateway over the same layout — its own chat locks
    await gw.start()
    await setter.start()
    try:
        _a, b = _ids(paths)
        gate.set()
        await gw.send_message("hi", chat_id="c1")  # the chat now exists
        started.clear()
        gate.clear()

        turn = asyncio.ensure_future(gw.send_message("again", chat_id="c1"))
        await started.wait()
        # Lands NOW, past this turn's resolution point and behind no lock it holds.
        assert await setter.update_chat("c1", model=b) is True
        assert await gw.chat_model("c1") == b  # the chat really has moved, mid-turn

        gate.set()
        assert await turn == "model-a"
        assert await gw.send_message("next", chat_id="c1") == "model-b"  # the NEXT one moves
    finally:
        await gw.close()
        await setter.close()


async def test_a_model_pinned_in_the_environment_still_wins_over_an_override(paths):
    _models(paths, "model-a", "model-b")
    gw = _gateway(paths, env={"AG2ASSISTANT_MODEL": "deployment-model"})
    await gw.start()
    try:
        _a, b = _ids(paths)
        assert gw._config.llm.env_pinned is True
        assert await gw.send_message("hi", chat_id="c1") == "deployment-model"
        await gw.update_chat("c1", model=b)
        assert await gw.send_message("hi", chat_id="c1") == "deployment-model"
    finally:
        await gw.close()


async def test_a_provider_pinned_without_a_model_does_not_suppress_an_override(paths):
    """A provider names no model, so it pins none — the Chat override still applies."""
    _models(paths, "model-a", "model-b")
    gw = _gateway(paths, env={"AG2ASSISTANT_LLM_PROVIDER": "gemini"})
    await gw.start()
    try:
        _a, b = _ids(paths)
        assert await gw.send_message("hi", chat_id="c1") == "model-a"
        await gw.update_chat("c1", model=b)
        assert await gw.send_message("hi", chat_id="c1") == "model-b"
    finally:
        await gw.close()


async def test_a_model_layered_in_outside_the_process_environment_does_not_pin(paths):
    """A pin is a deployment-level choice. ``resolve_config`` layers a saved Secret's
    env through the same call as the process env, so only the process env may pin —
    otherwise a Secret would disable every Chat override install-wide."""
    _models(paths, "model-a", "model-b")

    def build() -> Config:
        cfg = resolve_config({}, paths)
        apply_env_overrides(cfg, {"AG2ASSISTANT_MODEL": "from-a-secret"})  # the secret layer
        return cfg

    assert build().llm.env_pinned is False
    assert resolve_config({"AG2ASSISTANT_MODEL": "deployed"}, paths).llm.env_pinned is True

    gw = Gateway(
        config=build(),
        memory=False,
        agent_factory=model_naming_agent_factory(),
        config_factory=build,
    )
    await gw.start()
    try:
        _a, b = _ids(paths)
        assert await gw.send_message("hi", chat_id="c1") == "from-a-secret"
        await gw.update_chat("c1", model=b)
        assert await gw.send_message("hi", chat_id="c1") == "model-b"
    finally:
        await gw.close()


async def _run_thread(paths, tmp_path, *, task_model="", agent_factory=None, **kwargs):
    """A started gateway wired to a real TaskService, plus the stream_id of one Run of
    one Task — the thread a manual reply would be typed into."""
    store = TaskStore(path=tmp_path / "tasks.db")
    task = await store.create_task(name="T", prompt="p", model=task_model)
    run = await store.create_run(task.id)
    tasks = TaskService(
        config=resolve_config({}, paths),
        store=store,
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        **kwargs,
    )
    gw = _gateway(paths, task_service=tasks, agent_factory=agent_factory)
    tasks.set_gateway(gw)
    await gw.start()
    return gw, tasks, store, task, run.stream_id


async def test_a_manual_reply_in_a_run_thread_runs_on_the_task_model(paths, tmp_path):
    """The bug this ticket fixes: a reply typed into a Run's thread ran on the profile
    default. It now follows the Task, which sits under the Chat override."""
    _models(paths, "model-a", "model-b", "model-c")
    _a, b, _c = (cfg["id"] for cfg in LlmConfigStore(paths).list_configs())
    gw, _tasks, store, task, stream = await _run_thread(
        paths, tmp_path, task_model=b, summary_factory=fake_summary_factory()
    )
    try:
        assert await gw.send_message("hi", chat_id=stream) == "model-b"
        # The Task's own record is untouched by any of this — no migration (ADR 0025).
        assert (await store.get_task(task.id)).model == b
    finally:
        await gw.close()


async def test_a_chat_override_on_a_run_thread_beats_the_task_model(paths, tmp_path):
    """Ask one cheap follow-up about an expensive Run without editing the Task."""
    _models(paths, "model-a", "model-b", "model-c")
    _a, b, c = (cfg["id"] for cfg in LlmConfigStore(paths).list_configs())
    gw, _tasks, _store, _task, stream = await _run_thread(
        paths, tmp_path, task_model=b, summary_factory=fake_summary_factory()
    )
    try:
        await gw.send_message("hi", chat_id=stream)
        await gw.update_chat(stream, model=c)
        assert await gw.send_message("and?", chat_id=stream) == "model-c"
    finally:
        await gw.close()


async def test_a_dangling_override_in_a_run_thread_falls_to_the_task_model(paths, tmp_path):
    """Degradation walks to the NEXT layer, never straight to the bottom: deleting the
    model a Run thread was overridden to leaves the thread on the TASK's model, so
    story 31 survives a housekeeping delete (ADR 0025). ``effective_model`` — what
    /status and the WebUI render — must say the same thing the turn ran on."""
    _models(paths, "model-a", "model-b")
    _a, b = _ids(paths)
    gw, _tasks, _store, _task, stream = await _run_thread(
        paths, tmp_path, task_model=b, summary_factory=fake_summary_factory()
    )
    try:
        await gw.send_message("hi", chat_id=stream)
        await gw.update_chat(stream, model="c_deleted")
        assert await gw.send_message("and?", chat_id=stream) == "model-b"
        assert await gw.effective_model(stream) == b
        assert await gw.chat_model(stream) == ""  # no longer the Chat's say
    finally:
        await gw.close()


async def test_an_unusable_override_is_not_rescued_by_the_task_layer(paths, tmp_path):
    """Dangling is not the same as unusable: a model that still EXISTS but cannot run
    fails the turn exactly as an unusable install-wide Active does — the Task layer
    under it is not a rescue path, and ``effective_model`` still names the override."""
    _models(paths, "model-a", "model-b", "model-c")
    _a, b, c = (cfg["id"] for cfg in LlmConfigStore(paths).list_configs())
    gw, _tasks, _store, _task, stream = await _run_thread(
        paths,
        tmp_path,
        task_model=b,
        agent_factory=model_naming_agent_factory(unusable={"model-c"}),
        summary_factory=fake_summary_factory(),
    )
    try:
        await gw.send_message("hi", chat_id=stream)
        await gw.update_chat(stream, model=c)
        with pytest.raises(RuntimeError, match="cannot run"):
            await gw.send_message("and?", chat_id=stream)
        assert await gw.effective_model(stream) == c
    finally:
        await gw.close()


async def test_a_run_thread_whose_task_names_no_model_falls_through(paths, tmp_path):
    """The Task layer is optional, exactly as ``Task.model`` is: name no model and the
    thread inherits the profile's Active like any other Chat."""
    _models(paths, "model-a", "model-b")
    gw, _tasks, _store, _task, stream = await _run_thread(
        paths, tmp_path, task_model="", summary_factory=fake_summary_factory()
    )
    try:
        assert await gw.send_message("hi", chat_id=stream) == "model-a"
    finally:
        await gw.close()


async def test_the_runs_own_turn_still_runs_on_the_task_model(paths, tmp_path):
    """A caller that names a model outright — today only the Task service, for a Run's
    own turn — outranks the Chat override (ADR 0025). Overriding the thread retargets
    your follow-ups, never the automated work the Task was configured to do."""
    _models(paths, "model-a", "model-b", "model-c")
    _a, b, c = (cfg["id"] for cfg in LlmConfigStore(paths).list_configs())
    gw, _tasks, _store, _task, stream = await _run_thread(
        paths, tmp_path, task_model=b, summary_factory=fake_summary_factory()
    )
    try:
        await gw.send_message("hi", chat_id=stream)
        await gw.update_chat(stream, model=c)
        assert await gw.send_message("p", chat_id=stream, llm_config_id=b) == "model-b"
    finally:
        await gw.close()


async def test_an_ordinary_chat_never_consults_the_task_layer(paths, tmp_path):
    """The Task layer applies to ``task-run:`` streams only — every other Chat resolves
    the four layers it always did."""
    _models(paths, "model-a", "model-b", "model-c")
    _a, _b, c = (cfg["id"] for cfg in LlmConfigStore(paths).list_configs())
    gw, _tasks, _store, _task, _stream = await _run_thread(
        paths, tmp_path, task_model=_b, summary_factory=fake_summary_factory()
    )
    try:
        assert await gw.send_message("hi", chat_id="c1") == "model-a"
        await gw.update_chat("c1", model=c)
        assert await gw.send_message("hi", chat_id="c1") == "model-c"
    finally:
        await gw.close()


async def test_a_run_summary_ignores_the_task_model_and_the_override(paths, tmp_path):
    """The cheap-model carve-out (ADR 0025), the Run half: a Run on an expensive Task
    model, in a thread overridden to a third model, is still distilled by the PROFILE's
    cheap model."""
    _models(paths, "model-a", "model-b", "model-c")
    _a, b, c = (cfg["id"] for cfg in LlmConfigStore(paths).list_configs())
    built: list[Config] = []
    started, gate = asyncio.Event(), asyncio.Event()
    store = TaskStore(path=tmp_path / "tasks.db")
    task = await store.create_task(name="T", prompt="p", model=b)
    env = {"AG2ASSISTANT_AGGREGATE_MODEL": "cheap-one"}
    tasks = TaskService(
        config=resolve_config(env, paths),
        store=store,
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        summary_factory=fake_summary_factory(summary="one-liner", built=built),
    )
    gw = _gateway(
        paths,
        env=env,
        task_service=tasks,
        agent_factory=lambda config, **kw: _GatedAgent(config, started, gate),
    )
    tasks.set_gateway(gw)
    setter = _gateway(paths)  # a second gateway over the same layout — its own chat locks
    await gw.start()
    await setter.start()
    try:
        # The Run's own turn writes the thread's transcript doc, so an override can only
        # land while that turn is in flight — which is exactly the case to pin down.
        run = await tasks.start_run(task.id)
        await started.wait()
        assert await setter.update_chat(run.stream_id, model=c) is True
        gate.set()
        await asyncio.wait_for(tasks._jobs_done(), 5)

        assert (await tasks.get_run(run.id))["summary"] == "one-liner"
        assert await gw.chat_model(run.stream_id) == c  # the override was in place
        assert built and all(cfg.llm.model == "model-a" for cfg in built)
        assert all(cheap_model(cfg) == "cheap-one" for cfg in built)
    finally:
        await gw.close()
        await setter.close()


# --- A model chosen before the Chat exists ---


async def test_a_model_chosen_before_the_chat_exists_runs_the_first_message(paths, gw):
    """A client's switcher is live on a Chat with no messages yet: the choice rides
    the message that creates the Chat, which runs on it and keeps it from then on."""
    a, b = _ids(paths)
    assert await gw.send_message("hi", chat_id="c1", chat_model=b) == "model-b"
    assert await gw.chat_model("c1") == b
    assert await gw.send_message("again", chat_id="c1") == "model-b"
    assert LlmConfigStore(paths).active_id() == a


async def test_a_pre_send_choice_is_ignored_once_the_chat_exists(paths, gw):
    """Only the message that CREATES the Chat adopts one — it is not a per-message
    model, so a Chat that already chose (to inherit, or otherwise) is left alone."""
    _a, b = _ids(paths)
    await gw.send_message("hi", chat_id="c1")  # the chat now exists, inheriting
    assert await gw.send_message("again", chat_id="c1", chat_model=b) == "model-a"
    assert await gw.chat_model("c1") == ""


# --- Cross-client: an override set from a Channel is the one the WebUI reads ---


class _OneProfile:
    """A ProfileDirectory over a single running gateway — what the router resolves
    every message through."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    def available_profiles(self, surface: str):
        return (AvailableProfile("p", "P"),)

    def default_profile(self, connection: str) -> str:
        return "p"

    def gateway_for_profile(self, pid: str):
        return self.gateway if pid == "p" else None

    async def notify_channel(self, connection, chat_id, text) -> None: ...

    async def ask_channel(self, connection, chat_id, inquiry, question) -> None: ...

    async def retract_channel(self, connection, chat_id, inquiry) -> None: ...


def _telegram(text: str) -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id="1001",
        chat_id="tg-1",
        platform="telegram",
        connection="telegram",
        is_direct=True,
    )


async def test_a_model_set_from_a_channel_is_the_one_the_webui_reports(paths):
    """The two clients never disagree about one conversation: `/model` writes the same
    Chat override the single-chat GET renders (its ``model`` / ``effective_model``)."""
    a, b = _models(paths, "model-a", "model-b")
    gateway = _gateway(paths, env={"GEMINI_API_KEY": "shared-key"})
    await gateway.start()
    PairingStore(paths).add_account("telegram", "1001", platform="telegram")
    router = ChannelRouter(_OneProfile(gateway), paths)
    try:
        assert await router.handle(_telegram("hi")) == Reply("model-a")
        chat = PeerStore(paths).get_peer("telegram", "tg-1").chat

        assert isinstance(await router.handle(_telegram("/model MODEL-B")), Reply)
        assert await gateway.chat_model(chat) == b
        assert await gateway.effective_model(chat) == b

        assert isinstance(await router.choose(_telegram(""), "model:"), Reply)
        assert await gateway.chat_model(chat) == ""
        assert await gateway.effective_model(chat) == a
    finally:
        await gateway.close()


# --- REST facade ---


def _api_models(client) -> tuple[str, str]:
    """Two shared Text models through the real routes; the first is made Active."""
    ids = [
        client.post(
            "/api/llm-configs", json={"name": n.upper(), "type": "gemini", "model": n}
        ).json()["config"]["id"]
        for n in ("model-a", "model-b")
    ]
    client.post(f"/api/llm-configs/{ids[0]}/use")
    return ids[0], ids[1]


def test_patch_chat_model_sets_clears_and_reports(profile_app):
    """PATCH accepts a model beside title/starred; the single-chat GET reports it."""
    client, pid = profile_app
    a, b = _api_models(client)
    client.post(api(pid, "/message"), json={"text": "hi", "chat_id": "c1"})

    # a model-only patch is accepted (not an empty patch)
    r = client.patch(api(pid, "/chats/c1"), json={"model": b})
    assert r.status_code == 200 and r.json() == {"ok": True}
    body = client.get(api(pid, "/chats/c1")).json()
    assert body["model"] == b and body["effective_model"] == b

    # the empty string clears it, back to inheriting the Active model
    assert client.patch(api(pid, "/chats/c1"), json={"model": ""}).status_code == 200
    body = client.get(api(pid, "/chats/c1")).json()
    assert body["model"] == "" and body["effective_model"] == a


def test_the_get_reports_a_deleted_override_as_inheriting(profile_app):
    """A housekeeping delete in Settings → Models never breaks the switcher: the GET
    reports the Chat as inheriting, so the closed button names the model it will run
    on rather than falling to its placeholder."""
    client, pid = profile_app
    a, b = _api_models(client)
    client.post(api(pid, "/message"), json={"text": "hi", "chat_id": "c1"})
    client.patch(api(pid, "/chats/c1"), json={"model": b})

    assert client.delete(f"/api/llm-configs/{b}").status_code == 200
    body = client.get(api(pid, "/chats/c1")).json()
    assert body["model"] == "" and body["effective_model"] == a


def test_patch_chat_model_guards(profile_app):
    """A genuinely empty patch is still 400; an unknown chat is still 404."""
    client, pid = profile_app
    _a, b = _api_models(client)
    client.post(api(pid, "/message"), json={"text": "hi", "chat_id": "c1"})

    r = client.patch(api(pid, "/chats/c1"), json={})
    assert r.status_code == 400 and r.json()["error"] == "empty patch"
    assert client.patch(api(pid, "/chats/nope"), json={"model": b}).status_code == 404


def test_the_single_chat_read_names_the_pinned_model(profile_app_factory):
    """Under an env pin the client still gets something renderable: the pinned model
    itself, alongside the override the chat records but does not get to use."""
    client, pid = profile_app_factory(env={"AG2ASSISTANT_MODEL": "deployment-model"})
    _a, b = _api_models(client)
    client.post(api(pid, "/message"), json={"text": "hi", "chat_id": "c1"})
    client.patch(api(pid, "/chats/c1"), json={"model": b})

    body = client.get(api(pid, "/chats/c1")).json()
    assert body["model"] == b and body["effective_model"] == "deployment-model"


def test_a_first_stream_frame_carrying_a_model_records_the_override(profile_app):
    """The WebUI composer's pre-send choice rides the WebSocket frame the WebUI
    actually sends turns on — there is no Chat to patch yet — and the install-wide
    Active is untouched. ``POST /message`` deliberately carries no model: a
    per-message model is out of scope (ADR 0025)."""
    client, pid = profile_app
    a, b = _api_models(client)
    with client.websocket_connect(api(pid, "/stream?chat=w1")) as ws:
        while ws.receive_json().get("type") != "ready":
            pass
        ws.send_json({"text": "hi", "model": b})
        while ws.receive_json().get("type") != "turn_end":
            pass

    body = client.get(api(pid, "/chats/w1")).json()
    assert body["model"] == b and body["effective_model"] == b
    assert client.get("/api/llm-configs").json()["active"] == a


def test_the_chat_list_does_not_report_the_override(profile_app):
    """Nothing renders it on a drawer row, so the list response is not extended."""
    client, pid = profile_app
    _a, b = _api_models(client)
    client.post(api(pid, "/message"), json={"text": "hi", "chat_id": "c1"})
    client.patch(api(pid, "/chats/c1"), json={"model": b})

    row = next(c for c in client.get(api(pid, "/chats")).json()["chats"] if c["chat_id"] == "c1")
    assert "model" not in row
