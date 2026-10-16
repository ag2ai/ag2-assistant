"""Independent rendered copies through the Profile HTTP and Chat stream boundaries."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from ag2.events import ToolCallEvent
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from tests.support.apps import api, make_manager
from tests.support.cards import author, card_definition, replay, send, snapshot_message, write_card
from tests.support.fakes import ScriptedModels


def test_save_local_values_as_an_independent_file_that_survives_restart(paths):
    pid = ProfileRegistry(paths).create_profile("Copies", "#109e91").id
    models = ScriptedModels(
        lambda cfg, model: [
            author("draft_card", {"definition": card_definition(), "data": {"title": "Original"}}),
            "done",
        ]
    )
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        send(client, pid)
        source = next(e["data"] for e in replay(client, pid) if e["type"].endswith("EphemeralCard"))
        before_catalog = client.get(api(pid, "/cards")).json()
        assert not client.get(api(pid, "/files")).json()["files"]
        snapshot = snapshot_message(source)
        snapshot["data"] = {"title": "Locally entered", "extra": {"rows": [1, 2]}}
        request = {
            "surface_id": source["surface_id"],
            "message": snapshot,
            "path": "answer.card-instance.yaml",
            "request_id": "copy-1",
        }
        saved = client.post(api(pid, "/chats/drafts/card-instances/save"), json=request)
        assert saved.status_code == 200, saved.text
        result = saved.json()
        assert result["status"] == "saved" and result["history_recorded"]
        opened = client.get(api(pid, "/card-instances"), params={"path": result["path"]})
        assert opened.status_code == 200, opened.text
        envelope = opened.json()
        assert envelope["kind"] == "card-instance" and envelope["format_version"] == 1
        assert envelope["message"]["data"] == {
            "title": "Locally entered",
            "extra": {"rows": [1, 2]},
        }
        assert envelope["message"]["component"] == source["component"]
        assert envelope["message"]["surface_id"] == result["instance_id"]
        assert client.get(api(pid, "/cards")).json() == before_catalog
        assert len(client.get(api(pid, "/files")).json()["files"]) == 1
        assert (
            client.post(api(pid, "/chats/drafts/card-instances/save"), json=request).json()
            == result
        )
        events = replay(client, pid)
        assert len([e for e in events if e["type"].endswith("CardInstanceSaved")]) == 1
        assert next(e["data"] for e in events if e["type"].endswith("EphemeralCard"))["data"] == {
            "title": "Original"
        }
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        assert (
            client.post(api(pid, "/chats/drafts/card-instances/save"), json=request).json()
            == result
        )
        assert client.delete(api(pid, "/chats/drafts")).status_code == 200
        assert client.post(api(pid, "/skills/card-author/suppress")).status_code == 200
        assert client.post(api(pid, "/skills/rich-views/suppress")).status_code == 200
        assert (
            client.get(api(pid, "/card-instances"), params={"path": result["path"]}).json()
            == envelope
        )


def test_retries_collisions_source_edits_and_portable_copies_are_independent(paths):

    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Copies", "#109e91").id
    other = registry.create_profile("Other", "#109e91").id
    models = ScriptedModels(
        lambda cfg, model: [
            author("draft_card", {"definition": card_definition(), "data": {"title": "Original"}}),
            "done",
        ]
    )
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        send(client, pid)
        source = next(e["data"] for e in replay(client, pid) if e["type"].endswith("EphemeralCard"))
        snapshot = snapshot_message(source)
        request = {
            "surface_id": source["surface_id"],
            "message": snapshot,
            "path": "copy.card-instance.yaml",
            "request_id": "first",
        }
        save_url = api(pid, "/chats/drafts/card-instances/save")
        first = client.post(save_url, json=request).json()
        assert first["history_recorded"]
        raw_url = api(pid, "/files/raw")
        raw = client.get(raw_url, params={"path": first["path"]})
        transferred = client.post(
            api(pid, "/files/upload"), files={"files": ("backup.card-instance.yaml", raw.content)}
        )
        assert transferred.status_code == 200
        assert client.post(save_url, json=request).json() == first
        edited = yaml.safe_load(raw.text)
        edited["message"]["data"]["title"] = "Edited copy"
        edit_text = yaml.safe_dump(edited, sort_keys=False)
        assert (
            client.put(
                raw_url,
                params={"path": first["path"]},
                content=edit_text,
                headers={"If-Match": raw.headers["ETag"]},
            ).status_code
            == 200
        )
        assert client.post(save_url, json=request).json() == first
        assert client.get(raw_url, params={"path": first["path"]}).text == edit_text
        stale = client.put(
            raw_url,
            params={"path": first["path"]},
            content="stale text",
            headers={"If-Match": raw.headers["ETag"]},
        )
        assert stale.status_code == 409
        assert client.post(save_url, json={**request, "request_id": "collision"}).status_code == 409
        assert (
            client.post(
                save_url, json={**request, "path": "different.card-instance.yaml"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                save_url, json={**request, "message": {**snapshot, "title": "Different"}}
            ).status_code
            == 409
        )
        second = client.post(
            save_url, json={**request, "path": "second.card-instance.yaml", "request_id": "second"}
        ).json()
        assert second["instance_id"] != first["instance_id"]
        assert client.post(api(pid, "/files/mkdir"), json={"path": "notes"}).status_code == 200
        assert (
            client.post(
                api(pid, "/files/move"),
                json={"from": first["path"], "to": "notes/renamed.card-instance.yaml"},
            ).status_code
            == 200
        )
        read = client.get(
            api(pid, "/card-instances"), params={"path": "notes/renamed.card-instance.yaml"}
        ).json()
        assert read["instance_id"] == first["instance_id"]
        assert read["message"]["data"]["title"] == "Edited copy"
        downloaded = client.get(
            raw_url, params={"path": "notes/renamed.card-instance.yaml", "download": True}
        )
        uploaded = client.post(
            api(other, "/files/upload"),
            files={"files": ("transfer.card-instance.yaml", downloaded.content)},
        )
        assert uploaded.status_code == 200
        assert (
            client.get(
                api(other, "/card-instances"), params={"path": "transfer.card-instance.yaml"}
            ).json()
            == read
        )
        assert not [
            c for c in client.get(api(other, "/cards")).json()["cards"] if c["origin"] == "profile"
        ]
        assert (
            client.delete(raw_url, params={"path": "notes/renamed.card-instance.yaml"}).status_code
            == 200
        )
        events = replay(client, pid)
        assert next(e["data"] for e in events if e["type"].endswith("EphemeralCard"))["data"] == {
            "title": "Original"
        }
        assert len([e for e in events if e["type"].endswith("CardInstanceSaved")]) == 2


def test_invalid_instances_and_destinations_are_contained_and_source_is_repairable(paths):

    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Copies", "#109e91").id
    other = registry.create_profile("Other", "#109e91").id
    models = ScriptedModels(
        lambda cfg, model: [
            author("draft_card", {"definition": card_definition(), "data": {"title": "Original"}}),
            "done",
        ]
    )
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        send(client, pid)
        source = next(e["data"] for e in replay(client, pid) if e["type"].endswith("EphemeralCard"))
        snapshot = snapshot_message(source)
        request = {
            "surface_id": source["surface_id"],
            "message": snapshot,
            "path": "copy.card-instance.yaml",
            "request_id": "first",
        }
        save_url = api(pid, "/chats/drafts/card-instances/save")
        assert (
            client.post(api(other, "/chats/drafts/card-instances/save"), json=request).status_code
            == 404
        )
        assert (
            client.post(api(pid, "/chats/missing/card-instances/save"), json=request).status_code
            == 404
        )
        for path in [
            "../escape.card-instance.yaml",
            "/tmp/escape.card-instance.yaml",
            "notes/../escape.card-instance.yaml",
            "plain.yaml",
            "missing/copy.card-instance.yaml",
            "back\\slash.card-instance.yaml",
        ]:
            assert client.post(save_url, json={**request, "path": path}).status_code == 400
        root = Path(client.get(api(pid, "/files")).json()["root"])
        outside = paths.root / "unrelated"
        outside.mkdir()
        (outside / "protected.card-instance.yaml").write_text("Keep me")
        (root / "escape").symlink_to(outside, target_is_directory=True)
        (root / "link.card-instance.yaml").symlink_to(outside / "protected.card-instance.yaml")
        assert (
            client.post(
                save_url, json={**request, "path": "escape/new.card-instance.yaml"}
            ).status_code
            == 400
        )
        assert (
            client.post(save_url, json={**request, "path": "link.card-instance.yaml"}).status_code
            == 409
        )
        assert (
            client.get(
                api(pid, "/card-instances"), params={"path": "link.card-instance.yaml"}
            ).status_code
            == 400
        )
        assert (outside / "protected.card-instance.yaml").read_text() == "Keep me"
        (root / "directory.card-instance.yaml").mkdir()
        assert (
            client.post(
                save_url, json={**request, "path": "directory.card-instance.yaml"}
            ).status_code
            == 409
        )
        bad_messages = [
            {**snapshot, "version": "v99"},
            {**snapshot, "data": [1]},
            {**snapshot, "__event__": "ExecuteAnything"},
        ]
        for mutation in ["named", "missing", "cycle"]:
            broken = copy.deepcopy(snapshot)
            nodes = broken["component"]["_components"]
            if mutation == "named":
                nodes[1]["component"] = "Shelf"
            else:
                nodes[0]["child"] = "absent" if mutation == "missing" else "root"
                broken["component"]["child"] = nodes[0]["child"]
            bad_messages.append(broken)
        for broken in bad_messages:
            response = client.post(save_url, json={**request, "message": broken})
            assert response.status_code == 400, response.text
        large = {**snapshot, "data": {"blob": "x" * (5 * 1024 * 1024)}}
        assert client.post(save_url, json={**request, "message": large}).status_code == 413
        assert not (root / "copy.card-instance.yaml").exists()
        saved = client.post(save_url, json=request)
        assert saved.status_code == 200, saved.text
        raw_url = api(pid, "/files/raw")
        original = client.get(raw_url, params={"path": request["path"]})
        document = yaml.safe_load(original.text)
        unsupported = {**document, "format_version": 999}
        for content in [
            "invalid: [",
            yaml.safe_dump(unsupported),
            "kind: card-instance\ndata: &a [*a]",
            original.text.replace("v1.0", "v99"),
        ]:
            assert (
                client.put(raw_url, params={"path": request["path"]}, content=content).status_code
                == 200
            )
            invalid = client.get(api(pid, "/card-instances"), params={"path": request["path"]})
            assert invalid.status_code == 400, invalid.text
            assert client.get(raw_url, params={"path": request["path"]}).text == content
        assert (
            client.put(raw_url, params={"path": request["path"]}, content=original.text).status_code
            == 200
        )
        assert (
            client.get(api(pid, "/card-instances"), params={"path": request["path"]}).status_code
            == 200
        )


def test_an_older_composite_draft_keeps_nested_bindings_and_complete_local_values(paths):
    definition = card_definition("Composite")
    definition["fields"]["rows"] = {"type": "array", "items": {"type": "object"}}
    definition["required"].append("rows")
    definition["example"]["rows"] = [{"name": "Example", "note": "Initial"}]
    definition["layout"] = [
        {"id": "root", "component": "Card", "child": "body"},
        {"id": "body", "component": "Column", "children": ["heading", "rows", "save"]},
        {"id": "heading", "component": "Text", "text": {"path": "/title"}},
        {"id": "rows", "component": "List", "children": {"componentId": "row", "path": "/rows"}},
        {"id": "row", "component": "Card", "child": "rowbody"},
        {"id": "rowbody", "component": "Column", "children": ["label", "input"]},
        {"id": "label", "component": "Text", "text": {"path": "./name"}},
        {"id": "input", "component": "TextField", "label": "Note", "value": {"path": "./note"}},
        {
            "id": "save",
            "component": "Button",
            "child": "save_text",
            "action": {"event": {"name": "save_surface", "context": {"data": {"path": "/"}}}},
        },
        {"id": "save_text", "component": "Text", "text": "Apply values"},
    ]
    initial = {
        "title": "First version",
        "rows": [{"name": "One", "note": "A"}, {"name": "Two", "note": "B"}],
    }
    program = [author("draft_card", {"definition": definition, "data": initial}), "done"]
    models = ScriptedModels(lambda cfg, model: program)
    pid = ProfileRegistry(paths).create_profile("Versions", "#109e91").id
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        send(client, pid)
        old = next(e["data"] for e in replay(client, pid) if e["type"].endswith("EphemeralCard"))
        revised = copy.deepcopy(definition)
        revised["layout"][2]["text"] = "New layout"
        program[:] = [
            author(
                "draft_card",
                {
                    "definition": revised,
                    "data": {**initial, "title": "Second version"},
                    "draft_id": old["draft_id"],
                    "expected_version": 1,
                },
            ),
            "done",
        ]
        send(client, pid, "Revise")
        local = {
            "title": "First version",
            "rows": [{"name": "One", "note": "Local edit"}, {"name": "Two", "note": "B"}],
            "unbound": {"retain": True},
        }
        snapshot = snapshot_message(old)
        snapshot["data"] = local
        # An alternate transport representation is normalized without losing IDs.
        snapshot["components"] = snapshot["component"]["_components"]
        snapshot["component"] = {
            key: value for key, value in snapshot["component"].items() if key != "_components"
        }
        saved = client.post(
            api(pid, "/chats/drafts/card-instances/save"),
            json={
                "surface_id": old["surface_id"],
                "message": snapshot,
                "path": "old.card-instance.yaml",
                "request_id": "old",
            },
        )
        assert saved.status_code == 200, saved.text
        opened = client.get(
            api(pid, "/card-instances"), params={"path": "old.card-instance.yaml"}
        ).json()
        assert opened["message"]["component"] == old["component"]
        assert opened["message"]["data"] == local
        assert (
            client.post(
                api(pid, "/chats/drafts/cards/save"),
                json={
                    "draft_id": old["draft_id"],
                    "expected_version": 1,
                    "surface_id": old["surface_id"],
                    "name": "Old",
                    "filename": "old.card.yaml",
                    "request_id": "definition",
                },
            ).status_code
            == 409
        )
        original_bytes = client.get(
            api(pid, "/files/raw"), params={"path": "old.card-instance.yaml"}
        ).content
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
                            "surfaceId": old["surface_id"],
                            "sourceComponentId": "root__save",
                            "timestamp": "2026-10-06T10:00:00Z",
                            "context": {"data": {**initial, "title": "Updated in Chat"}},
                        },
                    },
                    "state": {
                        "surfaceId": old["surface_id"],
                        "data": {**initial, "title": "Updated in Chat"},
                    },
                }
            )
            while True:
                frame = ws.receive_json()
                if frame.get("event", {}).get("type", "").endswith("A2UISurfaceDataUpdated"):
                    break
        assert (
            client.get(api(pid, "/files/raw"), params={"path": "old.card-instance.yaml"}).content
            == original_bytes
        )
        assert any(
            e["type"].endswith("A2UISurfaceDataUpdated")
            and e["data"]["data"]["title"] == "Updated in Chat"
            for e in replay(client, pid)
        )


@pytest.mark.parametrize("origin", ["bundled", "global", "profile"])
def test_catalogued_copies_outlive_catalog_changes_and_their_source_chat(paths, origin):
    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Catalog copies", "#109e91").id
    script = "Checklist" if origin == "bundled" else "Shelf"
    data = (
        {"title": "Keep this answer", "items": ["One"]}
        if origin == "bundled"
        else {"title": "Keep this answer"}
    )
    models = ScriptedModels(
        lambda cfg, model: [
            ToolCallEvent(
                name="run_skill_script",
                arguments=json.dumps({"name": "rich-views", "script": script, "args": data}),
            ),
            "done",
        ]
    )
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        if origin != "bundled":
            directory = (
                paths.cards_dir
                if origin == "global"
                else Path(client.get(api(pid, "/files")).json()["root"]) / "cards"
            )
            definition_path = write_card(directory, "Shelf")
        send(client, pid)
        source = next(e["data"] for e in replay(client, pid) if e["type"].endswith("A2UISurface"))
        request = {
            "surface_id": source["surface_id"],
            "message": snapshot_message(source),
            "path": "retained.card-instance.yaml",
            "request_id": "retain",
        }
        response = client.post(api(pid, "/chats/drafts/card-instances/save"), json=request)
        assert response.status_code == 200, response.text
        retained = client.get(api(pid, "/card-instances"), params={"path": request["path"]}).json()
        if origin != "bundled":
            changed = yaml.safe_load(definition_path.read_text())
            changed["layout"][1]["text"] = "Changed definition"
            definition_path.write_text(yaml.safe_dump(changed))
            assert (
                client.get(api(pid, "/card-instances"), params={"path": request["path"]}).json()
                == retained
            )
            delete_url = "/api/cards" if origin == "global" else api(pid, "/cards")
            assert client.delete(delete_url, params={"name": "Shelf"}).status_code == 200
        else:
            assert (
                client.post(
                    "/api/cards/state", params={"name": script}, json={"enabled": False}
                ).status_code
                == 200
            )
        assert client.delete(api(pid, "/chats/drafts")).status_code == 200
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        assert client.post(api(pid, "/skills/rich-views/suppress")).status_code == 200
        assert (
            client.get(api(pid, "/card-instances"), params={"path": request["path"]}).json()
            == retained
        )


def test_a_file_write_failure_leaves_previous_files_and_chat_intact(paths):
    pid = ProfileRegistry(paths).create_profile("Failure", "#109e91").id
    models = ScriptedModels(
        lambda cfg, model: [
            author("draft_card", {"definition": card_definition(), "data": {"title": "Original"}}),
            "done",
        ]
    )
    manager = make_manager(paths, persist=True, model_factory=models)
    with TestClient(create_app(manager, persist=True)) as client:
        send(client, pid)
        source = next(e["data"] for e in replay(client, pid) if e["type"].endswith("EphemeralCard"))
        root = Path(client.get(api(pid, "/files")).json()["root"])
        root.mkdir(exist_ok=True)
        (root / "existing.txt").write_text("Keep existing content")
        request = {
            "surface_id": source["surface_id"],
            "message": snapshot_message(source),
            "path": "new.card-instance.yaml",
            "request_id": "failure",
        }
        root.chmod(0o500)
        try:
            response = client.post(api(pid, "/chats/drafts/card-instances/save"), json=request)
            assert response.status_code == 500, response.text
            assert not (root / request["path"]).exists()
            assert (root / "existing.txt").read_text() == "Keep existing content"
            assert not any(e["type"].endswith("CardInstanceSaved") for e in replay(client, pid))
        finally:
            root.chmod(0o700)
        assert (
            client.post(api(pid, "/chats/drafts/card-instances/save"), json=request).status_code
            == 200
        )
