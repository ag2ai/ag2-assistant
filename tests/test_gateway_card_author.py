"""Browser transport, Skill filtering and interactive draft parity."""

import json

from ag2.events import ToolCallEvent
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from tests.support.apps import api, make_manager
from tests.support.cards import card_definition
from tests.support.fakes import ScriptedModels


def author(script, args):
    return ToolCallEvent(
        name="run_skill_script",
        arguments=json.dumps(
            {
                "name": "card-author",
                "script": script,
                "args": args,
            }
        ),
    )


def replay(client, pid, chat="drafts"):
    events = []
    with client.websocket_connect(api(pid, f"/stream?chat={chat}")) as ws:
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "ready":
                return events
            if "event" in frame:
                events.append(frame["event"])


def send(client, pid, text="Draw", chat="drafts"):
    result = client.post(api(pid, "/message"), json={"text": text, "chat_id": chat})
    assert result.status_code == 200, result.text


def test_shell_save_survives_restart_and_skill_suppression_without_cross_profile_access(paths):
    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Author", "#109e91").id
    other = registry.create_profile("Other", "#109e91").id
    models = ScriptedModels(
        lambda cfg, model: [
            author(
                "draft_card", {"definition": card_definition(), "data": {"title": "Current books"}}
            ),
            "done",
        ]
    )
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        skills = {s["name"]: s for s in client.get(api(pid, "/skills")).json()["skills"]}
        assert skills["card-author"]["enabled"] and skills["card-author"]["available"]
        send(client, pid)
        event = next(e["data"] for e in replay(client, pid) if e["type"].endswith("EphemeralCard"))
        assert client.post(api(pid, "/skills/card-author/suppress")).status_code == 200
        send(client, pid, "Authoring disabled")
        assert len([e for e in replay(client, pid) if e["type"].endswith("EphemeralCard")]) == 1
    request = dict(
        draft_id=event["draft_id"],
        expected_version=1,
        surface_id=event["surface_id"],
        name="Saved shelf",
        filename="saved-shelf.card.yaml",
        request_id="shell-save",
    )
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        saved = client.post(api(pid, "/chats/drafts/cards/save"), json=request)
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "saved"
        row = next(
            c for c in client.get(api(pid, "/cards")).json()["cards"] if c["name"] == "Saved shelf"
        )
        assert row["origin"] == "profile" and row["available"]
        raw = api(pid, "/files/raw")
        opened = client.get(raw, params={"path": row["path"]})
        assert opened.status_code == 200
        edited = client.put(
            raw,
            params={"path": row["path"]},
            content=opened.text.replace("Saved shelf", "Edited shelf"),
            headers={"If-Match": opened.headers["ETag"]},
        )
        assert edited.status_code == 200
        disabled = client.post(
            api(pid, "/cards/state"), params={"name": "Edited shelf"}, json={"enabled": False}
        )
        assert disabled.status_code == 200
        assert not next(c for c in disabled.json()["cards"] if c["name"] == "Edited shelf")[
            "available"
        ]
        assert client.post(api(other, "/chats/drafts/cards/save"), json=request).status_code == 409
        assert client.post(api(pid, "/chats/other/cards/save"), json=request).status_code == 409
        assert (
            client.post(
                api(pid, "/chats/drafts/cards/save"), json={**request, "definition": {}}
            ).status_code
            == 422
        )


def test_ephemeral_inputs_actions_and_links_use_the_ordinary_surface_path(paths):
    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Author", "#109e91").id
    definition = card_definition("Interactive")
    definition["fields"]["note"] = {"type": "string"}
    definition["example"]["note"] = "Illustrative note"
    definition["layout"] = [
        {"id": "root", "component": "Card", "child": "body"},
        {"id": "body", "component": "Column", "children": ["input", "save", "ask", "link"]},
        {"id": "input", "component": "TextField", "label": "Note", "value": {"path": "/note"}},
        {
            "id": "save",
            "component": "Button",
            "child": "save_text",
            "action": {"event": {"name": "save_surface", "context": {"data": {"path": "/"}}}},
        },
        {"id": "save_text", "component": "Text", "text": "Keep values"},
        {
            "id": "ask",
            "component": "Button",
            "child": "ask_text",
            "action": {"event": {"name": "apply_note", "context": {"note": {"path": "/note"}}}},
        },
        {"id": "ask_text", "component": "Text", "text": "Apply"},
        {"id": "link", "component": "Link", "child": "link_text", "url": "https://example.com"},
        {"id": "link_text", "component": "Text", "text": "Reference"},
    ]
    program = [
        author(
            "draft_card", {"definition": definition, "data": {"title": "Real", "note": "Initial"}}
        ),
        "done",
    ]
    models = ScriptedModels(lambda cfg, model: program)
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        send(client, pid)
        event = next(e["data"] for e in replay(client, pid) if e["type"].endswith("EphemeralCard"))
        surface = event["surface_id"]
        program[:] = ["done"]
        with client.websocket_connect(api(pid, "/stream?chat=drafts")) as ws:
            while ws.receive_json().get("type") != "ready":
                pass
            ws.send_json(
                {
                    "type": "a2ui",
                    "message": {
                        "version": "v1.0",
                        "action": {
                            "name": "save_surface",
                            "surfaceId": surface,
                            "sourceComponentId": "root__save",
                            "timestamp": "2026-10-05T10:00:00Z",
                            "context": {"data": {"title": "Real", "note": "Kept"}},
                        },
                    },
                    "state": {"surfaceId": surface, "data": {"title": "Real", "note": "Kept"}},
                }
            )
            while True:
                frame = ws.receive_json()
                if frame.get("event", {}).get("type", "").endswith("A2UISurfaceDataUpdated"):
                    assert frame["event"]["data"]["data"]["note"] == "Kept"
                    break
            ws.send_json(
                {
                    "type": "a2ui",
                    "message": {
                        "version": "v1.0",
                        "action": {
                            "name": "apply_note",
                            "surfaceId": surface,
                            "sourceComponentId": "root__ask",
                            "timestamp": "2026-10-05T10:00:00Z",
                            "context": {"note": "Edited"},
                        },
                    },
                    "state": {"surfaceId": surface, "data": {"title": "Real", "note": "Edited"}},
                }
            )
            while ws.receive_json().get("type") != "turn_end":
                pass
        events = replay(client, pid)
        assert len([e for e in events if e["type"].endswith("EphemeralCard")]) == 1
        assert any(
            e["type"].endswith("A2UISurfaceDataUpdated") and e["data"]["data"]["note"] == "Edited"
            for e in events
        )
        assert any(
            e["type"].endswith("ModelRequest") and "apply_note" in str(e["data"]) for e in events
        )
        assert (
            client.post(
                api(pid, "/chats/drafts/cards/save"),
                json={
                    "draft_id": event["draft_id"],
                    "expected_version": 1,
                    "surface_id": surface,
                    "name": "Interactive",
                    "filename": "interactive.card.yaml",
                    "request_id": "interaction-save",
                },
            ).json()["status"]
            == "saved"
        )
        program[:] = [
            ToolCallEvent(
                name="run_skill_script",
                arguments=json.dumps(
                    {
                        "name": "rich-views",
                        "script": "Interactive",
                        "args": {"title": "Reuse", "note": "Fresh"},
                    }
                ),
            ),
            "done",
        ]
        send(client, pid, "Draw saved", chat="reuse")
        reused = next(
            e["data"] for e in replay(client, pid, "reuse") if e["type"].endswith("A2UISurface")
        )
        assert reused["component"] == event["component"]
        assert reused["data"] == {"title": "Reuse", "note": "Fresh"}
