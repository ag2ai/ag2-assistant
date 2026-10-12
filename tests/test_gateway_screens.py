"""Screens reference retained Files objects through the public Profile APIs."""

import json
import uuid
from datetime import UTC, datetime

import pytest
import yaml
from ag2.events import ToolCallEvent
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from tests.support.apps import api, make_manager
from tests.support.cards import replay, send
from tests.support.fakes import ScriptedModels


def instance():
    identity = str(uuid.uuid4())
    return {
        "kind": "card-instance",
        "format_version": 1,
        "instance_id": identity,
        "saved_at": datetime.now(UTC).isoformat(),
        "save_request_id": identity,
        "request_hash": "0" * 64,
        "message": {
            "surface_id": identity,
            "version": "v1.0",
            "catalog_id": "catalog",
            "component": {"id": "root", "component": "Text", "text": {"path": "/summary"}},
            "data": {
                "summary": "Last good",
                "_sources": {
                    "root": {
                        "tool": "get_weather",
                        "args": {"location": "Moscow"},
                        "fields": {"summary": {"type": "string"}},
                        "required": ["summary"],
                        "interval_seconds": 60,
                    }
                },
            },
            "title": "Reading",
            "intent": "",
        },
    }


def screen(path="reading.card-instance.yaml", title="Morning"):
    return {
        "kind": "screen",
        "format_version": 1,
        "title": title,
        "layout": [
            {"id": "root", "component": "Column", "children": ["first", "second"]},
            {"id": "first", "component": "CardInstance", "path": path},
            {"id": "second", "component": "CardInstance", "path": path},
        ],
    }


def upload(client, pid, path, document):
    response = client.post(
        api(pid, "/files/upload"), files={"files": (path, yaml.safe_dump(document))}
    )
    assert response.status_code == 200, response.text


def view(client, pid, path="morning.screen.yaml"):
    response = client.get(api(pid, "/screens/view"), params={"path": path})
    assert response.status_code == 200, response.text
    return response.json()


def test_screen_rename_preserves_references_and_survives_restart(paths):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    root = paths.profile_dir(pid) / "workspace"
    document = screen(title="Morning")
    with TestClient(create_app(make_manager(paths, persist=True))) as client:
        upload(client, pid, "reading.card-instance.yaml", instance())
        upload(client, pid, "morning.screen.yaml", document)
        original_instance = (root / "reading.card-instance.yaml").read_bytes()
        before = view(client, pid)
        response = client.patch(
            api(pid, "/screens/title"),
            json={"path": "morning.screen.yaml", "title": "  Утро ☀️  "},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"path": "morning.screen.yaml", "title": "Утро ☀️", "error": ""}
        assert yaml.safe_load((root / "morning.screen.yaml").read_bytes()) == {
            **document,
            "title": "Утро ☀️",
        }
        assert (root / "reading.card-instance.yaml").read_bytes() == original_instance
        after = view(client, pid)
        assert after["title"] == after["message"]["title"] == "Утро ☀️"
        assert after["message"]["component"] == before["message"]["component"]
        assert after["message"]["data"] == before["message"]["data"]
        assert after["sources"] == before["sources"]
        assert client.get(api(pid, "/chats")).json()["chats"] == []
    with TestClient(create_app(make_manager(paths, persist=True))) as client:
        assert client.get(api(pid, "/screens")).json()["screens"] == [response.json()]
        assert view(client, pid)["title"] == "Утро ☀️"


def test_screen_rename_does_not_require_loading_instances(paths):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    with TestClient(create_app(make_manager(paths))) as client:
        upload(client, pid, "morning.screen.yaml", screen("missing.card-instance.yaml"))
        response = client.patch(
            api(pid, "/screens/title"),
            json={"path": "morning.screen.yaml", "title": "New name"},
        )
        assert response.status_code == 200, response.text
        assert client.get(api(pid, "/screens")).json()["screens"][0]["title"] == "New name"
        assert (
            client.get(
                api(pid, "/screens/view"), params={"path": "morning.screen.yaml"}
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    ("path", "title", "status"),
    [
        ("morning.screen.yaml", " ", 400),
        ("morning.screen.yaml", "x" * 201, 400),
        ("missing.screen.yaml", "New", 404),
        ("../morning.screen.yaml", "New", 400),
        ("/morning.screen.yaml", "New", 400),
        ("morning.card-instance.yaml", "New", 400),
        ("linked.screen.yaml", "New", 400),
        ("linked-dir/morning.screen.yaml", "New", 400),
        ("invalid.screen.yaml", "New", 400),
    ],
)
def test_screen_rename_rejects_invalid_targets_without_writing(
    paths, tmp_path, path, title, status
):
    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Screens", "#109e91").id
    other = registry.create_profile("Other", "#109e91").id
    root = paths.profile_dir(pid) / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "morning.screen.yaml"
    original = yaml.safe_dump(screen()).encode()
    external.write_bytes(original)
    with TestClient(create_app(make_manager(paths))) as client:
        upload(client, pid, "morning.screen.yaml", screen())
        upload(client, pid, "invalid.screen.yaml", {"title": "Bad"})
        upload(client, other, "other.screen.yaml", screen(title="Other"))
        (root / "linked.screen.yaml").symlink_to(external)
        (root / "linked-dir").symlink_to(outside, target_is_directory=True)
        response = client.patch(api(pid, "/screens/title"), json={"path": path, "title": title})
        assert response.status_code == status, response.text
        assert (root / "morning.screen.yaml").read_bytes() == original
        assert external.read_bytes() == original
        assert yaml.safe_load((root / "invalid.screen.yaml").read_bytes()) == {"title": "Bad"}
        response = client.patch(
            api(other, "/screens/title"), json={"path": "morning.screen.yaml", "title": "Other"}
        )
        assert response.status_code == 404
        assert (root / "morning.screen.yaml").read_bytes() == original


@pytest.mark.parametrize("binding", ["/summary", "summary"])
def test_shared_references_refresh_once_and_publish_to_other_views_without_chat(paths, binding):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    results = iter(["Fresh once", "Manual update"])

    async def weather(**args):
        assert args == {"location": "Moscow"}
        return json.dumps({"summary": next(results)})

    with TestClient(
        create_app(make_manager(paths), card_source_tools={"get_weather": weather})
    ) as client:
        saved = instance()
        saved["message"]["component"]["text"]["path"] = binding
        upload(client, pid, "reading.card-instance.yaml", saved)
        upload(client, pid, "copy.card-instance.yaml", instance())
        upload(client, pid, "morning.screen.yaml", screen())
        upload(client, pid, "evening.screen.yaml", screen(title="Evening"))
        original = view(client, pid)
        assert (
            original["message"]["component"]["_components"][1]["text"]["path"]
            == "/_cards/first/summary"
        )
        with client.websocket_connect(api(pid, "/card-sources/stream")) as ws:
            assert ws.receive_json()["type"] == "ready"
            target = {"path": "reading.card-instance.yaml", "source_id": "root", "trigger": "shown"}
            response = client.post(api(pid, "/card-sources/refresh"), json=target)
            assert response.status_code == 200, response.text
            event = ws.receive_json()["event"]
            assert event["type"].endswith("CardSourceUpdated")
            assert event["data"]["path"] == target["path"]
            assert event["data"]["data"]["summary"] == "Fresh once"
            again = client.post(api(pid, "/card-sources/refresh"), json=target).json()
            assert again["events"][0]["data"]["data"]["summary"] == "Fresh once"
            for name in ("morning.screen.yaml", "evening.screen.yaml"):
                message = view(client, pid, name)["message"]
                assert [card["summary"] for card in message["data"]["_cards"].values()] == [
                    "Fresh once",
                    "Fresh once",
                ]
            manual = client.post(
                api(pid, "/card-sources/refresh"), json={**target, "trigger": "manual"}
            )
            assert manual.json()["events"][0]["data"]["data"]["summary"] == "Manual update"
        assert (
            client.get(
                api(pid, "/card-instances"), params={"path": "copy.card-instance.yaml"}
            ).json()["message"]["data"]["summary"]
            == "Last good"
        )
        assert client.get(api(pid, "/chats")).json()["chats"] == []
        raw = client.get(api(pid, "/files/raw"), params={"path": "morning.screen.yaml"}).text
        assert yaml.safe_load(raw) == screen()


def test_file_refresh_broadcasts_without_retaining_event_history(paths):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    readings = iter(range(140))

    async def weather(**args):
        return json.dumps({"summary": f"Reading {next(readings)}"})

    manager = make_manager(paths)
    with TestClient(create_app(manager, card_source_tools={"get_weather": weather})) as client:
        upload(client, pid, "reading.card-instance.yaml", instance())
        with client.websocket_connect(api(pid, "/card-sources/stream")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for reading in range(140):
                response = client.post(
                    api(pid, "/card-sources/refresh"),
                    json={"path": "reading.card-instance.yaml", "source_id": "root"},
                )
                assert response.status_code == 200, response.text
                assert ws.receive_json()["event"]["data"]["data"]["summary"] == f"Reading {reading}"
            stream = manager.get(pid).require_gateway().card_sources.file_stream
            assert client.portal.call(stream.history.get_events) == []
            assert (
                client.get(
                    api(pid, "/card-instances"), params={"path": "reading.card-instance.yaml"}
                ).json()["message"]["data"]["summary"]
                == "Reading 139"
            )


def screen_call(script, args):
    return ToolCallEvent(
        name="run_skill_script",
        arguments=json.dumps({"name": "screens", "script": script, "args": args}),
    )


def test_agent_creates_instances_and_screen_then_layout_edits_survive_restart(paths):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    initial = screen("market.card-instance.yaml")
    models = ScriptedModels(
        lambda cfg, model: [
            ToolCallEvent(name="load_skill", arguments='{"name":"screens"}'),
            screen_call(
                "save_instance",
                {
                    "path": "market.card-instance.yaml",
                    "card": "MarketBoard",
                    "fields": {
                        "_parameters": {"symbols": "AAPL", "title": "Market"},
                        "title": "Market",
                        "quotes": [],
                    },
                },
            ),
            screen_call("save_screen", {"path": "morning.screen.yaml", "document": initial}),
            "Open your morning dashboard from Screens.",
        ]
    )
    with TestClient(create_app(make_manager(paths, persist=True, model_factory=models))) as client:
        send(client, pid, text="Make me a market dashboard")
        result = view(client, pid)
        assert result["sources"]["first"]["source"]["parameters"] == {
            "symbols": "AAPL",
            "title": "Market",
        }
        assert client.get(api(pid, "/screens")).json()["screens"] == [
            {"path": "morning.screen.yaml", "title": "Morning", "error": ""}
        ]
        initial["layout"][0]["children"] = ["second", "first"]
        response = client.put(
            api(pid, "/files/raw"),
            params={"path": "morning.screen.yaml"},
            content=yaml.safe_dump(initial),
        )
        assert response.status_code == 200, response.text
        assert view(client, pid)["message"]["component"]["children"] == ["second", "first"]
        history = replay(client, pid)
        assert not any(event["type"].endswith("ToolErrorEvent") for event in history)
        loaded = next(
            event["data"]
            for event in history
            if event["type"].endswith("ToolResultEvent") and event["data"]["name"] == "load_skill"
        )
        assert '<skill_content name="screens">' in loaded["result"]["data"]["parts"][0]["content"]
        assert not any(event["type"].endswith("A2UISurface") for event in history)
    with TestClient(create_app(make_manager(paths, persist=True))) as client:
        assert view(client, pid)["message"]["component"]["children"] == ["second", "first"]
        assert client.delete(api(pid, "/chats/drafts")).status_code == 200
        assert view(client, pid)["sources"]["second"]["path"] == "market.card-instance.yaml"


@pytest.mark.parametrize(
    "target",
    [
        "../other.card-instance.yaml",
        "/etc/reading.card-instance.yaml",
        "missing.card-instance.yaml",
    ],
)
def test_missing_and_unauthorized_references_fail_without_substitution(paths, target):
    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Screens", "#109e91").id
    other = registry.create_profile("Other", "#e95034").id
    with TestClient(create_app(make_manager(paths))) as client:
        upload(client, other, "missing.card-instance.yaml", instance())
        upload(client, pid, "morning.screen.yaml", screen(target))
        result = client.get(api(pid, "/screens/view"), params={"path": "morning.screen.yaml"})
        assert result.status_code in {400, 404}
        assert "message" not in result.json()
        assert (
            client.get(
                api(other, "/card-instances"), params={"path": "missing.card-instance.yaml"}
            ).status_code
            == 200
        )


def test_invalid_relative_binding_is_rejected_and_repeated_binding_is_namespaced(paths):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    saved = instance()
    saved["message"]["component"]["text"]["path"] = "./summary"
    with TestClient(create_app(make_manager(paths))) as client:
        upload(client, pid, "reading.card-instance.yaml", saved)
        upload(client, pid, "morning.screen.yaml", screen())
        response = client.get(api(pid, "/screens/view"), params={"path": "morning.screen.yaml"})
        assert response.status_code == 400
        assert "relative binding outside a template" in response.text
        saved["message"]["component"] = {
            "id": "root",
            "component": "List",
            "children": {"componentId": "row", "path": "/rows"},
            "_components": [
                {
                    "id": "root",
                    "component": "List",
                    "children": {"componentId": "row", "path": "/rows"},
                },
                {"id": "row", "component": "Text", "text": {"path": "./summary"}},
            ],
        }
        saved["message"]["data"]["rows"] = [{"summary": "Repeated"}]
        assert (
            client.put(
                api(pid, "/files/raw"),
                params={"path": "reading.card-instance.yaml"},
                content=yaml.safe_dump(saved),
            ).status_code
            == 200
        )
        nodes = view(client, pid)["message"]["component"]["_components"]
        assert nodes[1]["children"] == {"componentId": "first/row", "path": "/_cards/first/rows"}
        assert nodes[2]["text"] == {"path": "./summary"}


def test_invalid_screen_is_listed_for_repair_and_symlink_escape_is_refused(paths, tmp_path):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    root = paths.profile_dir(pid) / "workspace"
    with TestClient(create_app(make_manager(paths))) as client:
        bad = screen()
        bad["layout"][0]["children"] = ["unknown"]
        upload(client, pid, "invalid.screen.yaml", bad)
        assert "unknown id" in client.get(api(pid, "/screens")).json()["screens"][0]["error"]
        outside = tmp_path / "external.card-instance.yaml"
        outside.write_text(yaml.safe_dump(instance()))
        (root / "linked.card-instance.yaml").symlink_to(outside)
        upload(client, pid, "morning.screen.yaml", screen("linked.card-instance.yaml"))
        assert (
            client.get(
                api(pid, "/screens/view"), params={"path": "morning.screen.yaml"}
            ).status_code
            == 400
        )


def test_explicit_file_reference_in_chat_reads_current_instance(paths):
    pid = ProfileRegistry(paths).create_profile("References", "#109e91").id
    models = ScriptedModels(
        lambda cfg, model: [
            screen_call("show_instance", {"path": "reading.card-instance.yaml"}),
            "Your saved Card.",
        ]
    )

    async def weather(**args):
        return json.dumps({"summary": "Updated through a Screen"})

    with TestClient(
        create_app(
            make_manager(paths, persist=True, model_factory=models),
            card_source_tools={"get_weather": weather},
        )
    ) as client:
        upload(client, pid, "reading.card-instance.yaml", instance())
        upload(client, pid, "morning.screen.yaml", screen())
        send(client, pid, "Show my saved reading Card")
        reference = next(
            event["data"]
            for event in replay(client, pid)
            if event["type"].endswith("CardInstanceReference")
        )
        assert reference["file_path"] == "reading.card-instance.yaml"
        assert reference["data"]["summary"] == "Last good"
        assert (
            client.post(
                api(pid, "/card-sources/refresh"),
                json={"path": reference["file_path"], "source_id": "root"},
            ).status_code
            == 200
        )
        reference = next(
            event["data"]
            for event in replay(client, pid)
            if event["type"].endswith("CardInstanceReference")
        )
        assert reference["data"]["summary"] == "Updated through a Screen"
        assert (
            view(client, pid)["message"]["data"]["_cards"]["first"]["summary"]
            == reference["data"]["summary"]
        )
        assert (
            client.delete(
                api(pid, "/files/raw"), params={"path": reference["file_path"]}
            ).status_code
            == 200
        )
        broken = next(
            event["data"]
            for event in replay(client, pid)
            if event["type"].endswith("CardInstanceReference")
        )
        assert broken["data"] == {}
        assert broken["title"] == "Instance unavailable"


def test_screen_creation_never_overwrites_or_executes_retained_custom_code(paths):
    pid = ProfileRegistry(paths).create_profile("Screens", "#109e91").id
    saved = instance()
    saved["message"]["data"]["_sources"]["root"] = {
        "code": "print('unapproved')",
        "fields": {"summary": {"type": "string"}},
    }
    models = ScriptedModels(
        lambda cfg, model: [
            screen_call(
                "save_screen",
                {"path": "morning.screen.yaml", "document": screen(title="Replacement")},
            ),
            "Done.",
        ]
    )
    with TestClient(create_app(make_manager(paths, persist=True, model_factory=models))) as client:
        upload(client, pid, "reading.card-instance.yaml", saved)
        upload(client, pid, "morning.screen.yaml", screen())
        send(client, pid, "Create an existing Screen")
        assert view(client, pid)["title"] == "Morning"
        response = client.post(
            api(pid, "/card-sources/refresh"),
            json={"path": "reading.card-instance.yaml", "source_id": "root"},
        )
        assert response.json()["status"] == "approval_required"
        assert view(client, pid)["message"]["data"]["_cards"]["first"]["summary"] == "Last good"
