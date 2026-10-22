"""The AG2 Space channel: the relay bridge's task files in, result and proactive files
out, driven without a bridge, a gateway or a network."""

import json
import os
from pathlib import Path

import pytest

from assistant import channels
from assistant.channels.ag2space import (
    CLAIM_SUFFIX,
    GUEST_REPLY,
    NO_SEND,
    AG2SpaceChannel,
    answered_marker,
    pending_tasks,
    prune_answered,
    render,
)
from assistant.channels.base import PUSH_CHANNELS, InboundMessage
from assistant.channels.router import Ack, ChannelRouter, Choose, Nothing, Option, Refuse, Reply
from assistant.connections import ConnectionStore
from assistant.pairing import PairingStore
from assistant.profiles import CHANNEL_PLATFORMS, CHANNEL_TOKEN_ENVS
from tests.test_channel_router import FakeDirectory

OWNER = "@alice:ag2.space"
ROOM = "!room:ag2.space"
CONNECTION = "cn-space"

TASK = """id: task-abc123
timestamp: 2026-10-20T10:00:00Z
task: what is on my calendar today?
source: ag2space
channel_id: {room}
sender_name: alice
user_id: {user}
access_tier: {tier}

===SKILL INSTRUCTIONS (follow before any other action)===
1. CONTEXT-FIRST: reconstruct the room thread
"""


def _task(tier="owner", user=OWNER, room=ROOM, tid="task-abc123") -> str:
    return TASK.format(room=room, user=user, tier=tier).replace("task-abc123", tid)


class StubRouter:
    """Records what the adapter hands over and answers with scripted outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.handled: list[InboundMessage] = []

    def paired(self, inbound) -> bool:
        return True

    async def handle(self, inbound, **kw):
        self.handled.append(inbound)
        return self.outcomes.pop(0) if self.outcomes else Reply("the answer")


@pytest.fixture
def ws(tmp_path) -> Path:
    w = tmp_path / "workspace"
    for d in ("tasks", "results", "state"):
        (w / d).mkdir(parents=True)
    return w


async def _channel(ws, *outcomes) -> tuple[AG2SpaceChannel, StubRouter]:
    """A started channel over ``ws`` whose poll loop is not running: tests poll."""
    router = StubRouter(*outcomes)
    ch = AG2SpaceChannel(workspace=ws, owner=OWNER, connection=CONNECTION)
    ch._router = router  # start() would also spawn the loop
    return ch, router


# --- registration -------------------------------------------------------------------


def test_the_platform_is_registered_with_its_two_settings():
    assert "ag2space" in CHANNEL_PLATFORMS
    assert CHANNEL_TOKEN_ENVS["ag2space"] == ("AG2SPACE_WORKSPACE", "AG2SPACE_OWNER")
    assert channels.TOKEN_ARGS["AG2SPACE_WORKSPACE"] == "workspace"
    assert channels.TOKEN_ARGS["AG2SPACE_OWNER"] == "owner"
    assert "ag2space" in PUSH_CHANNELS  # task-run outcomes are pushed back to the room


def test_get_channel_builds_it_from_the_connection_tokens(tmp_path):
    ch = channels.get_channel("ag2space", connection="c1", workspace=str(tmp_path), owner=OWNER)
    assert isinstance(ch, AG2SpaceChannel)
    assert ch.connection == "c1" and ch.workspace == tmp_path and ch.owner == OWNER


def test_it_refuses_to_build_without_a_workspace_or_an_owner(tmp_path):
    with pytest.raises(ValueError):
        AG2SpaceChannel(workspace="", owner=OWNER)
    with pytest.raises(ValueError):
        AG2SpaceChannel(workspace=str(tmp_path), owner="")


def test_a_matrix_id_pairs_as_an_account_id(paths):
    """An AG2 Space id is identity, never a handle invitation (ADR 0021)."""
    account = PairingStore(paths).add_account(CONNECTION, OWNER, "ag2space")
    assert account.account_id == OWNER and not account.pending
    assert PairingStore(paths).is_paired(CONNECTION, OWNER)


# --- inbound: a task file becomes a message to the room's chat ------------------------


async def test_an_owner_task_becomes_one_message_and_one_result(ws):
    ch, router = await _channel(ws, Reply("the answer"))
    (ws / "tasks" / "task-abc123.txt").write_text(_task())

    assert await ch.poll() == 1

    (inbound,) = router.handled
    assert inbound.text == "what is on my calendar today?"  # the engine-only block is cut
    assert inbound.chat_id == ROOM and inbound.sender_id == OWNER
    assert inbound.platform == "ag2space" and inbound.connection == CONNECTION
    assert inbound.is_direct and inbound.mentioned and inbound.sender_name == "alice"
    assert (ws / "results" / "task-abc123.txt").read_text() == "the answer\n"
    # claimed, never deleted: the bridge archives it once the result is delivered
    assert [p.name for p in (ws / "tasks").iterdir()] == ["task-abc123" + CLAIM_SUFFIX]
    assert answered_marker(ws / "state", "task-abc123").exists()


async def test_a_finished_task_is_not_run_again_while_delivery_is_pending(ws):
    ch, router = await _channel(ws)
    (ws / "tasks" / "task-once.txt").write_text(_task(tid="task-once"))
    for _ in range(3):  # three polls before the bridge archives it
        await ch.poll()
    assert len(router.handled) == 1
    assert (ws / "results" / "task-once.txt").read_text() == "the answer\n"


async def test_a_restart_after_the_result_does_not_repeat_the_turn(ws):
    ch, router = await _channel(ws)
    # What the previous process left: the claim, the result, the marker.
    (ws / "tasks" / ("task-r" + CLAIM_SUFFIX)).write_text(_task(tid="task-r"))
    (ws / "results" / "task-r.txt").write_text("the answer\n")
    answered_marker(ws / "state", "task-r").parent.mkdir(parents=True)
    answered_marker(ws / "state", "task-r").touch()
    assert pending_tasks(ws / "tasks", ws / "state") == []
    await ch.poll()
    assert router.handled == []
    # Without the marker the same claim is a turn cut off mid-way, retried once.
    answered_marker(ws / "state", "task-r").unlink()
    await ch.poll()
    await ch.poll()
    assert len(router.handled) == 1


async def test_markers_go_once_the_bridge_archives_the_task(ws):
    ch, _ = await _channel(ws)
    (ws / "tasks" / "task-a.txt").write_text(_task(tid="task-a"))
    await ch.poll()
    marker = answered_marker(ws / "state", "task-a")
    assert marker.exists()
    (ws / "tasks" / "archive").mkdir()
    (ws / "tasks" / ("task-a" + CLAIM_SUFFIX)).rename(ws / "tasks" / "archive" / "task-a.txt")
    (ws / "results" / "task-a.txt").unlink()
    assert prune_answered(ws / "state", ws / "tasks") == 1
    assert not marker.exists()
    assert prune_answered(ws / "state" / "never", ws / "tasks") == 0


async def test_guest_and_team_tasks_are_refused_without_a_turn(ws):
    ch, router = await _channel(ws)
    for tier in ("guest", "team", ""):
        tid = f"task-{tier or 'none'}"
        (ws / "tasks" / f"{tid}.txt").write_text(_task(tier=tier, user="@bob:x", tid=tid))
    await ch.poll()
    assert router.handled == []
    for tier in ("guest", "team", "none"):
        assert (ws / "results" / f"task-{tier}.txt").read_text().strip() == GUEST_REPLY


async def test_an_empty_task_is_archived_without_a_post(ws):
    ch, router = await _channel(ws)
    (ws / "tasks" / "task-e.txt").write_text("id: task-e\naccess_tier: owner\n")
    await ch.poll()
    assert router.handled == []
    assert (ws / "results" / "task-e.txt").read_text().strip() == NO_SEND


async def test_a_task_the_bridge_archived_first_is_left_alone(ws):
    ch, router = await _channel(ws)
    await ch._handle(ws / "tasks" / "task-gone.txt")  # the rename fails
    await ch._handle(ws / "tasks" / ("task-gone" + CLAIM_SUFFIX))  # the read fails
    assert router.handled == [] and list((ws / "results").iterdir()) == []


async def test_one_bad_task_never_stops_the_poll(ws, monkeypatch):
    ch, router = await _channel(ws)
    real = ch._handle

    async def handle(path):
        if path.name.startswith("task-bad"):
            raise RuntimeError("boom")
        await real(path)

    monkeypatch.setattr(ch, "_handle", handle)
    (ws / "tasks" / "task-bad.txt").write_text(_task(tid="task-bad"))
    (ws / "tasks" / "task-ok.txt").write_text(_task(tid="task-ok"))
    os.utime(ws / "tasks" / "task-bad.txt", (1, 1))  # oldest first, so it fails first
    assert await ch.poll() == 1
    assert (ws / "results" / "task-ok.txt").read_text() == "the answer\n"
    assert not (ws / "results" / "task-bad.txt").exists()


# --- outcomes: what each one becomes in the room ---------------------------------------


def test_outcomes_render_as_result_bodies():
    assert render(Reply("hi")) == "hi"
    assert render(Refuse("no")) == "no"
    assert render(Ack()) == NO_SEND  # fed into a running turn: its answer lands there
    assert render(Nothing()) == NO_SEND
    choose = Choose("Which profile?", (Option("Work", "t1"), Option("Play", "t2")))
    assert render(choose) == "Which profile?\n\n1. Work\n2. Play"


async def test_a_steered_message_is_archived_silently(ws):
    ch, _ = await _channel(ws, Ack())
    (ws / "tasks" / "task-s.txt").write_text(_task(tid="task-s"))
    await ch.poll()
    assert (ws / "results" / "task-s.txt").read_text().strip() == NO_SEND


# --- outbound: a message no task asked for ---------------------------------------------


async def test_notify_writes_a_proactive_file_addressed_to_the_room(ws):
    ch, _ = await _channel(ws)
    await ch.notify(ROOM, "Your 9am run finished: 3 PRs reviewed.")
    await ch.notify("!other:ag2.space", "second")
    files = sorted((ws / "results").iterdir())
    assert [f.name.startswith("proactive-") and f.name.endswith(".txt") for f in files] == [
        True,
        True,
    ]
    assert files[0].read_text() == f"[channel: {ROOM}]\nYour 9am run finished: 3 PRs reviewed.\n"
    assert files[1].read_text() == "[channel: !other:ag2.space]\nsecond\n"
    assert not [f for f in (ws / "results").iterdir() if f.name.endswith(".tmp")]


async def test_ask_posts_the_question_as_text_and_retract_is_quiet(ws):
    ch, _ = await _channel(ws)
    choose = Choose("Deploy now?", (Option("Yes", "t1"), Option("Later", "t2")))
    await ch.ask(ROOM, "inq-1", choose)
    (f,) = list((ws / "results").iterdir())
    assert f.read_text() == f"[channel: {ROOM}]\nDeploy now?\n\n1. Yes\n2. Later\n"
    await ch.retract(ROOM, "inq-1")
    assert len(list((ws / "results").iterdir())) == 1


# --- through the real router -------------------------------------------------------------


async def test_the_real_router_runs_the_turn_in_the_rooms_chat_and_pushes_back(ws, paths):
    """End to end minus the bridge: a paired owner's task reaches the gateway with the
    room as the chat, and a task-run outcome for that room comes back as a proactive file."""
    directory = FakeDirectory("work", reply="the answer")
    router = ChannelRouter(directory, paths)
    PairingStore(paths).add_account(CONNECTION, OWNER, "ag2space")
    ch = AG2SpaceChannel(workspace=ws, owner=OWNER, connection=CONNECTION)
    await ch.start(router)
    try:
        (ws / "tasks" / "task-abc123.txt").write_text(_task())
        assert await ch.poll() == 1
    finally:
        await ch.stop()

    (call,) = directory.gateways["work"].calls
    assert call["text"] == "what is on my calendar today?"
    assert (ws / "results" / "task-abc123.txt").read_text() == "the answer\n"
    # The Peer the turn ran for is the room, so a run outcome can find its way back.
    peer = router._peers.get_peer(CONNECTION, ROOM)
    assert peer is not None and peer.platform == "ag2space"
    await router.push(CONNECTION, ROOM, "later: done")
    assert directory.pushed == [(CONNECTION, ROOM, "later: done")]


async def test_an_unpaired_writer_gets_no_turn(ws, paths):
    router = ChannelRouter(FakeDirectory("work"), paths)
    ch = AG2SpaceChannel(workspace=ws, owner=OWNER, connection=CONNECTION)
    ch._router = router
    (ws / "tasks" / "task-abc123.txt").write_text(_task(user="@mallory:ag2.space"))
    await ch.poll()
    assert (ws / "results" / "task-abc123.txt").read_text().strip() == NO_SEND


# --- the install: a Connection on the platform starts like any other ---------------------


def test_a_connection_on_the_platform_is_created_and_started(paths, tmp_path):
    from fastapi.testclient import TestClient

    from assistant.gateway.app import create_app
    from tests.support.apps import make_manager, no_loopback_code_reader

    manager = make_manager(paths, env={})
    with TestClient(create_app(manager, code_reader=no_loopback_code_reader)) as client:
        r = client.post(
            "/api/connections",
            json={
                "platform": "ag2space",
                "tokens": {"AG2SPACE_WORKSPACE": str(tmp_path), "AG2SPACE_OWNER": OWNER},
            },
        )
        assert r.status_code == 200, r.text
        entry = r.json()
        assert entry["platform"] == "ag2space" and entry["active"] is True
        cid = entry["id"]
        live = manager.channels[cid]
        assert live.tokens == {"workspace": str(tmp_path), "owner": OWNER}
        r = client.post("/api/connections", json={"platform": "ag2space", "tokens": {}})
        assert r.status_code == 400 and "AG2SPACE_WORKSPACE" in r.json()["error"]
    assert json.loads((paths.root / "connections.json").read_text())
    assert ConnectionStore(paths).connections_for("ag2space")[0].id == cid
