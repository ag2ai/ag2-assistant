"""History pages retain complete turns and stable cursors across compaction."""

import pytest
from ag2.events import ModelMessage, ModelRequest, ModelResponse
from ag2.knowledge import SqliteKnowledgeStore
from ag2.knowledge.log import EventLogWriter
from fastapi.testclient import TestClient

from assistant.config import Config
from assistant.events import A2UISurface
from assistant.gateway.core import Gateway
from assistant.gateway.history import event_cursor, event_page
from tests.support.apps import api, make_profile_app
from tests.support.fakes import fake_agent_factory


def turns(count):
    return [
        event
        for index in range(count)
        for event in (
            ModelRequest.ensure_request([f"question {index}"]),
            ModelResponse(ModelMessage(f"answer {index}")),
        )
    ]


def test_pages_cover_every_event_once_in_chronological_complete_turns():
    events = [A2UISurface("intro", component={"Text": {"text": "Intro"}}), *turns(25)]
    page = event_page(events, 10)
    assert page.events == events[-20:]
    restored = page.events
    while page.before:
        page = event_page(events, 10, before=page.before)
        restored = page.events + restored
    assert restored == events
    assert page.start == event_cursor(events[0])
    assert event_page([], 10).before is None
    assert event_page([], 10).start is None


def test_a_page_keeps_every_event_of_a_long_turn():
    events = turns(11)
    card = A2UISurface("last-card", component={"Text": {"text": "Card"}})
    events.insert(-1, card)
    page = event_page(events, 1)
    assert page.events == events[-3:]
    with pytest.raises(ValueError):
        event_page(events, 1, before="0" * 64)


async def test_compaction_and_restart_preserve_history_cursors(paths):
    config = Config.for_paths(paths)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    events = turns(15)
    archive = SqliteKnowledgeStore(str(config.data_dir / "profile.db"))
    await EventLogWriter(archive).persist_dropped("c1", events[:20])
    archive.close()
    for _ in range(2):
        gw = Gateway(config, memory=False, agent_factory=fake_agent_factory())
        await gw.start()
        try:
            await gw._writer.persist("c1", events[20:])
            page = event_page(await gw.replay_events("c1"), 5)
            assert page.events[0].to_dict() == events[20].to_dict()
            older = event_page(await gw.replay_events("c1"), 5, before=page.before)
            assert [event.to_dict() for event in older.events] == [
                event.to_dict() for event in events[10:20]
            ]
        finally:
            await gw.close()


def test_profile_history_route_and_transcript_limit(paths):
    app, pid = make_profile_app(paths)
    prefix = api(pid, "/chats/c1")
    with TestClient(app) as client:
        for index in range(12):
            assert (
                client.post(
                    api(pid, "/message"), json={"chat_id": "c1", "text": f"question {index}"}
                ).status_code
                == 200
            )
        gw = app.state.profiles.get(pid).require_gateway()
        stream = client.portal.call(gw.stream_for, "c1")
        client.portal.call(stream.history.replace, turns(12))
        latest = client.get(prefix + "/events?limit=5")
        assert latest.status_code == 200
        body = latest.json()
        questions = [
            event["data"]["parts"][0]["content"]
            for event in body["events"]
            if event["type"].endswith(".ModelRequest")
        ]
        assert questions == [f"question {index}" for index in range(7, 12)]
        older = client.get(prefix + "/events", params={"before": body["before"], "limit": 5}).json()
        assert older["start"] != body["start"]
        assert (
            client.get(prefix + "?limit=1").json()["messages"]
            == client.get(prefix).json()["messages"][-1:]
        )
        assert client.get(prefix + "/events?limit=0").status_code == 422
        assert client.get(prefix + "/events?limit=51").status_code == 422
        assert client.get(prefix + "/events?before=invalid").status_code == 422
        assert client.get(prefix + "/events?before=" + "0" * 64).status_code == 409
        assert client.get(api(pid, "/chats/missing/events")).json() == {
            "events": [],
            "before": None,
            "start": None,
        }
        with client.websocket_connect(api(pid, "/stream?chat=c1&history_limit=5")) as socket:
            replay = []
            while True:
                frame = socket.receive_json()
                if frame.get("type") == "ready":
                    assert frame["history"] == {"before": body["before"], "start": body["start"]}
                    break
                replay.append(frame["event"])
            assert replay == body["events"]
