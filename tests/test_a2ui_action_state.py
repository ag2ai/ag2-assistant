"""A click on an A2UI Button and the Card instance's data model.

The browser holds the values the user typed; a click sends that model alongside
the action envelope and the gateway persists it. These pin the gateway seam: what
survives a click, what a silent client leaves alone, and what is refused.
"""

import json

from fastapi.testclient import TestClient

from assistant.a2ui_skill import A2UI_SKILL
from assistant.skills import SkillStateStore
from tests.support.apps import make_profile_app
from tests.support.chat import click, logged, run, wait_for
from tests.support.fakes import FakeReply, FakeRunMixin, fake_agent_factory

SURFACE = "surface-1"


class RecordingAgent(FakeRunMixin):
    """A fake agent that keeps every turn's text, so a test can read what a click said."""

    def __init__(self):
        self.asked: list[str] = []
        self.tools = []

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        self.asked.append(str(msg[0]) if msg else "")
        return FakeReply("ok")


def _msg(name="apply", *, context=None, surface_id=SURFACE):
    return {
        "version": "v1.0",
        "action": {
            "name": name,
            "surfaceId": surface_id,
            "sourceComponentId": "btn",
            "timestamp": "2026-09-22T00:00:00Z",
            "context": context or {},
        },
    }


def _models(events, *, surface_id=SURFACE) -> list[dict]:
    """The data models the A2UISurfaceDataUpdated events on `surface_id` carried."""
    return [
        event["data"]["data"]
        for event in events
        if event["type"].endswith("A2UISurfaceDataUpdated")
        and event["data"]["surface_id"] == surface_id
    ]


def test_a_click_persists_the_whole_model_the_client_holds(paths):
    """The Button's context names one argument; the instance's model holds three.
    All three survive — the context never stands in for the model."""
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(
            client,
            pid,
            _msg(context={"colours": ["red"]}),
            {
                "surfaceId": SURFACE,
                "data": {"selectedColours": ["red"], "note": "keep me", "day": 3},
            },
        )
        events = logged(client, app, pid)

    assert _models(events) == [{"selectedColours": ["red"], "note": "keep me", "day": 3}]


def test_a_click_inside_a_repeated_row_leaves_the_array_whole(paths):
    """A per-row Button carries one row in its context. The bound array keeps every
    row, and the clicked row keeps the fields its context did not name."""
    rows = [
        {"symbol": "AG2", "qty": 1, "note": "first"},
        {"symbol": "ABC", "qty": 2, "note": "second"},
        {"symbol": "XYZ", "qty": 3, "note": "third"},
    ]
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(
            client,
            pid,
            _msg("buy", context={"symbol": "ABC"}),
            {"surfaceId": SURFACE, "data": {"holdings": rows}},
        )
        events = logged(client, app, pid)

    assert _models(events) == [{"holdings": rows}]


def test_the_persisted_model_comes_back_on_a_reload(paths):
    """A later read of the log holds the click's model, so the values the user typed
    really are persisted rather than only echoed once."""
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(
            client,
            pid,
            _msg(context={"colours": ["red"]}),
            {"surfaceId": SURFACE, "data": {"note": "typed before clicking"}},
        )
        replayed = client.get(f"/api/p/{pid}/chats/c1/events").json()["events"]

    cards = [
        e
        for e in replayed
        if e["type"] == "CUSTOM" and e["name"].endswith("A2UISurfaceDataUpdated")
    ]
    assert [e["value"]["data"] for e in cards] == [{"note": "typed before clicking"}]


def test_a_click_carrying_no_model_leaves_the_instance_alone(paths):
    """An older bundle — or any non-browser client — sends no model. Publishing an
    empty one would blank the instance, so nothing is published at all."""
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(client, pid, _msg(context={"colours": ["red"]}))
        events = logged(client, app, pid)

    assert _models(events) == []
    assert any(event["type"].endswith("A2UIActionSubmitted") for event in events)


def test_a_model_naming_another_card_instance_is_not_written(paths):
    """The model is the clicked instance's own. One naming a different instance is
    written nowhere — not to that instance, and not to the clicked one."""
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(client, pid, _msg(), {"surfaceId": "somewhere-else", "data": {"note": "not mine"}})
        events = logged(client, app, pid)

    assert _models(events) == []
    assert _models(events, surface_id="somewhere-else") == []


def test_an_oversized_model_is_refused_and_the_click_still_reaches_the_agent(paths):
    """A client-supplied body is bounded. Over the bound nothing is persisted, the
    refusal is said out loud rather than dropped silently, and the click is still
    handed to the agent."""
    agent = RecordingAgent()
    app, pid = make_profile_app(paths, agent_factory=fake_agent_factory(agent))
    with TestClient(app) as client:
        response = click(
            client,
            pid,
            _msg(),
            {"surfaceId": SURFACE, "data": {"blob": "x" * (2 * 1024 * 1024)}},
        )
        wait_for(lambda: agent.asked)
        events = logged(client, app, pid)

    assert _models(events) == []
    assert response.status_code == 413
    assert any("apply" in text for text in agent.asked)


def test_the_bound_counts_bytes_not_characters(paths):
    """A model of text that is cheap in characters and dear in bytes is still under
    the bound — a user who types in Cyrillic does not lose what they typed."""
    written = {"note": "я" * 100_000}  # 100k characters, 200k UTF-8 bytes
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(client, pid, _msg(), {"surfaceId": SURFACE, "data": written})
        events = logged(client, app, pid)

    assert len(json.dumps(written).encode()) > 256 * 1024  # \uXXXX-escaped, it would refuse
    assert len(json.dumps(written, ensure_ascii=False).encode()) < 256 * 1024
    assert _models(events) == [written]


def test_the_agents_action_text_still_carries_the_declared_context(paths):
    """The Button's declared context is its contract with the agent, and the model
    the client sends does not change it."""
    agent = RecordingAgent()
    app, pid = make_profile_app(paths, agent_factory=fake_agent_factory(agent))
    with TestClient(app) as client:
        click(
            client,
            pid,
            _msg("apply_preferences", context={"colours": ["red"]}),
            {"surfaceId": SURFACE, "data": {"selectedColours": ["red"]}},
        )
        wait_for(lambda: agent.asked)

    assert len(agent.asked) == 1
    text = agent.asked[0]
    assert "apply_preferences" in text
    assert str({"colours": ["red"]}) in text
    assert "selectedColours" not in text


def test_a_registered_action_ignores_the_model_the_client_sends(paths):
    """`run_server_action`'s own messages stay the only thing that updates a
    registered action's model."""
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(
            client,
            pid,
            _msg("save_surface", context={"data": {"kept": "by the server"}}),
            {"surfaceId": SURFACE, "data": {"kept": "by the client"}},
        )
        run(client, pid, text="ping")
        events = logged(client, app, pid)

    assert _models(events) == [{"kept": "by the server"}]


def test_the_model_rides_outside_the_a2ui_message(paths):
    """`parse_incoming_message` sees the envelope it always saw: a message carrying
    the model inside itself is not a model the gateway reads."""
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        message = _msg()
        message["state"] = {"surfaceId": SURFACE, "data": {"note": "smuggled"}}
        click(client, pid, message)
        events = logged(client, app, pid)

    assert _models(events) == []


def test_the_bound_is_on_the_model_not_the_whole_frame(paths):
    """A model comfortably inside the bound is persisted whole, however many keys
    and rows it holds."""
    rows = [{"symbol": f"S{i}", "note": "x" * 100} for i in range(200)]
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(client, pid, _msg(), {"surfaceId": SURFACE, "data": {"holdings": rows}})
        events = logged(client, app, pid)

    assert len(json.dumps({"holdings": rows})) < 256 * 1024
    assert _models(events) == [{"holdings": rows}]


def test_a_click_on_a_drawn_card_still_works_with_rich_views_off(paths):
    """Turning the feature off stops new Cards, never the ones a chat already has:
    the click keeps its model and still reaches the agent."""
    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, False)
    agent = RecordingAgent()
    app, pid = make_profile_app(paths, agent_factory=fake_agent_factory(agent))
    with TestClient(app) as client:
        click(
            client,
            pid,
            _msg("apply", context={"colours": ["red"]}),
            {"surfaceId": SURFACE, "data": {"colours": ["red"], "size": "m"}},
        )
        wait_for(lambda: agent.asked)
        events = logged(client, app, pid)

    assert _models(events) == [{"colours": ["red"], "size": "m"}]
    assert len(agent.asked) == 1 and "apply" in agent.asked[0]


def test_a_saved_surface_still_saves_with_rich_views_off(paths):
    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, False)
    app, pid = make_profile_app(paths)
    with TestClient(app) as client:
        click(client, pid, _msg("save_surface", context={"data": {"kept": "yes"}}))
        run(client, pid, text="ping")
        events = logged(client, app, pid)

    assert _models(events) == [{"kept": "yes"}]
