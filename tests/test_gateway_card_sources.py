"""Independent source refresh through the public Profile Gateway and typed events."""

import asyncio
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from ag2.events import ToolCallEvent
from fastapi.testclient import TestClient

from assistant.events import A2UISurface
from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from tests.support.apps import api, make_manager
from tests.support.cards import replay, send, snapshot_message
from tests.support.fakes import ScriptedModels


def document(source=None):
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
            "catalog_id": "https://ag2.ai/assistant/a2ui/catalog.json",
            "component": {"id": "root", "component": "Text", "text": {"path": "/summary"}},
            "data": {
                "summary": "Last good",
                "unrelated": "Keep",
                "_sources": {
                    "root": source
                    or {
                        "tool": "get_weather",
                        "args": {"location": {"parameter": "place"}},
                        "parameters": {"place": "Moscow"},
                        "fields": {"summary": {"type": "string"}},
                        "required": ["summary"],
                        "path": "",
                        "interval_seconds": 60,
                    }
                },
            },
            "title": "Reading",
            "intent": "",
        },
    }


def upload(client, pid, path="reading.card-instance.yaml", source=None):
    response = client.post(
        api(pid, "/files/upload"), files={"files": (path, yaml.safe_dump(document(source)))}
    )
    assert response.status_code == 200, response.text
    return path


def test_file_refresh_updates_only_its_source_through_typed_events_without_a_turn(paths):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id

    async def weather(**args):
        assert args == {"location": "Moscow"}
        return json.dumps({"summary": "Fresh"})

    manager = make_manager(paths, persist=True)
    with TestClient(create_app(manager, card_source_tools={"get_weather": weather})) as client:
        path = upload(client, pid)
        original = client.get(api(pid, "/card-instances"), params={"path": path}).json()
        response = client.post(
            api(pid, "/card-sources/refresh"), json={"path": path, "source_id": "root"}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "updated"
        event = result["events"][-1]
        assert event["type"].endswith("CardSourceUpdated")
        assert event["data"]["data"]["summary"] == "Fresh"
        assert event["data"]["data"]["unrelated"] == "Keep"
        assert event["data"]["data"]["_sources"] == original["message"]["data"]["_sources"]
        assert client.get(api(pid, "/chats")).json()["chats"] == []


def test_failed_and_invalid_source_results_keep_last_good_values_and_copies_independent(paths):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    results = iter(['{"summary":"Fresh"}', "Network unavailable", '{"summary":123}'])

    async def weather(**args):
        return next(results)

    with TestClient(
        create_app(make_manager(paths, persist=True), card_source_tools={"get_weather": weather})
    ) as client:
        path = upload(client, pid)
        content = client.get(api(pid, "/files/raw"), params={"path": path}).content
        assert (
            client.post(
                api(pid, "/files/upload"), files={"files": ("copy.card-instance.yaml", content)}
            ).status_code
            == 200
        )
        request = {"path": path, "source_id": "root"}
        assert (
            client.post(api(pid, "/card-sources/refresh"), json=request).json()["status"]
            == "updated"
        )
        for _ in range(2):
            result = client.post(api(pid, "/card-sources/refresh"), json=request).json()
            assert result["status"] == "error"
            assert result["events"][0]["data"]["data"]["summary"] == "Fresh"
            assert result["events"][0]["data"]["error"]
        assert (
            client.get(
                api(pid, "/card-instances"), params={"path": "copy.card-instance.yaml"}
            ).json()["message"]["data"]["summary"]
            == "Last good"
        )


def test_drawn_weather_retains_parameters_and_refreshes_after_catalog_deletion(paths):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    models = ScriptedModels(
        lambda cfg, model: [
            ToolCallEvent(
                name="run_skill_script",
                arguments=json.dumps(
                    {
                        "name": "rich-views",
                        "script": "WeatherPanel",
                        "args": {
                            "location": "Moscow",
                            "condition": "sunny",
                            "summary": "Original",
                            "rows": [],
                            "_parameters": {"location": "Moscow", "units": "celsius"},
                        },
                    }
                ),
            ),
            "done",
        ]
    )

    async def weather(**args):
        assert args == {"location": "Moscow", "units": "celsius"}
        return '{"location":"Moscow","condition":"rainy","summary":"Fresh","rows":[]}'

    with TestClient(
        create_app(
            make_manager(paths, persist=True, model_factory=models),
            card_source_tools={"get_weather": weather},
        )
    ) as client:
        send(client, pid)
        surface = next(e["data"] for e in replay(client, pid) if e["type"].endswith("A2UISurface"))
        source = surface["data"]["_sources"]["root"]
        assert source["parameters"] == {"location": "Moscow", "units": "celsius"}
        count = len(models.requests)
        saved = client.post(
            api(pid, "/chats/drafts/card-instances/save"),
            json={
                "surface_id": surface["surface_id"],
                "message": snapshot_message(surface),
                "path": "saved.card-instance.yaml",
                "request_id": "save",
            },
        )
        assert saved.status_code == 200, saved.text
        assert client.post(api(pid, "/cards/suppress?name=WeatherPanel")).status_code == 200
        result = client.post(
            api(pid, "/card-sources/refresh"),
            json={"chat_id": "drafts", "surface_id": surface["surface_id"], "source_id": "root"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "updated", result.text
        assert len(models.requests) == count
        assert (
            client.get(
                api(pid, "/card-instances"), params={"path": "saved.card-instance.yaml"}
            ).json()["message"]["data"]["summary"]
            == "Original"
        )


def test_custom_code_needs_profile_version_approval_and_explicit_stable_secret_binding(paths):
    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Sources", "#109e91").id
    other = registry.create_profile("Other", "#109e91").id
    source = {
        "code": "import json, os, sys\nprint(json.dumps({'summary': json.loads(sys.argv[1])['place'] + ':' + ('selected' if os.environ.get('DATA_KEY') else 'missing')}))\n",
        "args": {"place": {"parameter": "place"}},
        "parameters": {"place": "Moscow"},
        "secret_names": ["DATA_KEY"],
        "fields": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    manager = make_manager(
        paths,
        persist=True,
        env={"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"},
    )
    with TestClient(create_app(manager)) as client:
        path = upload(client, pid, source=source)
        request = {"path": path, "source_id": "root"}
        result = client.post(api(pid, "/card-sources/refresh"), json=request).json()
        assert result["status"] == "approval_required"
        version = result["events"][0]["data"]["code_version"]
        secret = client.post(
            "/api/secrets", json={"name": "Source key", "value": "sensitive-source-value"}
        ).json()["secret"]
        approval = {
            **request,
            "code_version": version,
            "approved": True,
            "secrets": {"DATA_KEY": secret["id"]},
        }
        assert client.post(api(pid, "/card-sources/approval"), json=approval).status_code == 200
        result = client.post(api(pid, "/card-sources/refresh"), json=request)
        assert result.json()["status"] == "updated", result.text
        assert result.json()["events"][0]["data"]["data"]["summary"] == "Moscow:selected"
        raw = client.get(api(pid, "/files/raw"), params={"path": path}).content
        assert b"sensitive-source-value" not in raw
        assert (
            client.post(api(other, "/files/upload"), files={"files": (path, raw)}).status_code
            == 200
        )
        assert (
            client.post(api(other, "/card-sources/refresh"), json=request).json()["status"]
            == "approval_required"
        )
        client.post(api(pid, "/card-sources/approval"), json={**approval, "approved": False})
        assert (
            client.post(api(pid, "/card-sources/refresh"), json=request).json()["status"]
            == "approval_required"
        )


@pytest.mark.parametrize("target", ["file", "chat"])
def test_composite_sources_refresh_independently_even_when_requests_overlap(paths, target):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    barrier = threading.Barrier(2)

    async def weather(**args):
        await asyncio.to_thread(barrier.wait, 5)
        return '{"summary":"Weather fresh"}'

    async def quotes(**args):
        await asyncio.to_thread(barrier.wait, 5)
        return '{"summary":"Quotes fresh"}'

    manager = make_manager(paths, persist=True)
    with TestClient(
        create_app(
            manager,
            card_source_tools={"get_weather": weather, "get_quotes": quotes},
        )
    ) as client:
        file = document()
        source = file["message"]["data"].pop("_sources")["root"]
        file["message"]["data"].update(
            {
                "_cards": {
                    "weather": {"summary": "Weather old", "note": "Keep"},
                    "quotes": {"summary": "Quotes old"},
                },
                "_sources": {
                    "weather": {**source, "path": "/_cards/weather"},
                    "quotes": {**source, "tool": "get_quotes", "path": "/_cards/quotes"},
                },
            }
        )
        if target == "file":
            path = "composite.card-instance.yaml"
            assert (
                client.post(
                    api(pid, "/files/upload"),
                    files={"files": (path, yaml.safe_dump(file))},
                ).status_code
                == 200
            )
            request = {"path": path}
        else:
            send(client, pid)
            replay(client, pid)
            gateway = manager.get(pid).require_gateway()
            assert client.portal is not None
            client.portal.call(
                gateway.emit_event,
                "drafts",
                A2UISurface(
                    "composite",
                    component=file["message"]["component"],
                    data=file["message"]["data"],
                ),
            )
            request = {"chat_id": "drafts", "surface_id": "composite"}
        with ThreadPoolExecutor() as pool:
            pending = [
                pool.submit(
                    client.post,
                    api(pid, "/card-sources/refresh"),
                    json={**request, "source_id": name},
                )
                for name in ("weather", "quotes")
            ]
            assert all(result.result().json()["status"] == "updated" for result in pending)
        if target == "file":
            current = client.get(api(pid, "/card-instances"), params={"path": path}).json()[
                "message"
            ]["data"]
        else:
            current = [
                e["data"]["data"]
                for e in replay(client, pid)
                if e["type"].endswith("CardSourceUpdated")
            ][-1]
        assert current["_cards"] == {
            "weather": {"summary": "Weather fresh", "note": "Keep"},
            "quotes": {"summary": "Quotes fresh"},
        }
        assert current["unrelated"] == "Keep"


@pytest.mark.parametrize("change", ["edit", "move", "delete"])
def test_inflight_refresh_does_not_overwrite_edits_or_revive_removed_targets(paths, change):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    started, release = threading.Event(), threading.Event()

    async def weather(**args):
        started.set()
        assert await asyncio.to_thread(release.wait, 5)
        return '{"summary":"Obsolete"}'

    with TestClient(
        create_app(make_manager(paths, persist=True), card_source_tools={"get_weather": weather})
    ) as client:
        path = upload(client, pid)
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(
                client.post,
                api(pid, "/card-sources/refresh"),
                json={"path": path, "source_id": "root"},
            )
            assert started.wait(5)
            raw_url = api(pid, "/files/raw")
            if change == "edit":
                raw = client.get(raw_url, params={"path": path})
                changed = yaml.safe_load(raw.text)
                changed["message"]["data"]["summary"] = "Authored edit"
                assert (
                    client.put(
                        raw_url,
                        params={"path": path},
                        content=yaml.safe_dump(changed),
                        headers={"If-Match": raw.headers["ETag"]},
                    ).status_code
                    == 200
                )
            elif change == "move":
                assert (
                    client.post(
                        api(pid, "/files/move"),
                        json={"from": path, "to": "moved.card-instance.yaml"},
                    ).status_code
                    == 200
                )
            else:
                assert client.delete(raw_url, params={"path": path}).status_code == 200
            release.set()
            assert pending.result().json()["status"] == "discarded"
        opened = client.get(api(pid, "/card-instances"), params={"path": path})
        if change == "edit":
            assert opened.json()["message"]["data"]["summary"] == "Authored edit"
        else:
            assert opened.status_code == 404


def test_custom_artifact_and_secret_references_survive_restart_but_changed_code_needs_approval(
    paths,
):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    source = {
        "code": "import json, helper\nprint(json.dumps({'summary': helper.value}))\n",
        "files": {"helper.py": "value = 'Retained helper'\n"},
        "fields": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    env = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}
    with TestClient(create_app(make_manager(paths, persist=True, env=env))) as client:
        path = upload(client, pid, source=source)
        request = {"path": path, "source_id": "root"}
        first = client.post(api(pid, "/card-sources/refresh"), json=request).json()
        version = first["events"][0]["data"]["code_version"]
        assert (
            client.post(
                api(pid, "/card-sources/approval"),
                json={**request, "code_version": version, "approved": True},
            ).status_code
            == 200
        )
    with TestClient(create_app(make_manager(paths, persist=True, env=env))) as client:
        result = client.post(api(pid, "/card-sources/refresh"), json=request).json()
        assert result["status"] == "updated"
        assert result["events"][0]["data"]["data"]["summary"] == "Retained helper"
        raw = client.get(api(pid, "/files/raw"), params={"path": path})
        copied = client.post(
            api(pid, "/files/upload"),
            files={"files": ("same-code.card-instance.yaml", raw.content)},
        )
        assert copied.status_code == 200
        assert (
            client.post(
                api(pid, "/card-sources/refresh"),
                json={**request, "path": "same-code.card-instance.yaml"},
            ).json()["status"]
            == "updated"
        )
        changed = yaml.safe_load(raw.text)
        changed["message"]["data"]["_sources"]["root"]["files"]["helper.py"] = (
            "value = 'Changed helper'\n"
        )
        assert (
            client.put(
                api(pid, "/files/raw"),
                params={"path": path},
                content=yaml.safe_dump(changed),
                headers={"If-Match": raw.headers["ETag"]},
            ).status_code
            == 200
        )
        assert (
            client.post(api(pid, "/card-sources/refresh"), json=request).json()["status"]
            == "approval_required"
        )
        assert (
            client.post(
                api(pid, "/card-sources/approval"),
                json={**request, "code_version": version, "approved": True},
            ).status_code
            == 409
        )


@pytest.mark.parametrize(
    "mutation", ["unregistered", "consent", "remote_schema", "bad_schema", "traversal"]
)
def test_portable_metadata_cannot_grant_execution_or_select_arbitrary_tools(paths, mutation):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    source = document()["message"]["data"]["_sources"]["root"]
    if mutation == "unregistered":
        source["tool"] = "run_shell_command"
    elif mutation == "consent":
        source["approved"] = True
    elif mutation == "remote_schema":
        source["fields"] = {"summary": {"$ref": "https://example.com/fields.json"}}
    elif mutation == "bad_schema":
        source["fields"] = {"summary": {"type": "made-up"}}
    else:
        source.pop("tool")
        source["code"] = "print('{}')"
        source["files"] = {"../escape.py": "print('bad')"}
    with TestClient(create_app(make_manager(paths, persist=True))) as client:
        path = upload(client, pid, source=source)
        assert client.get(api(pid, "/card-instances"), params={"path": path}).status_code == 400
        assert (
            client.post(
                api(pid, "/card-sources/refresh"), json={"path": path, "source_id": "root"}
            ).status_code
            == 400
        )


@pytest.mark.parametrize(
    "code, message",
    [
        ("print('x' * 100001)", "size limit"),
        ("import time\ntime.sleep(31)\nprint('{}')", "timed out"),
    ],
)
def test_custom_execution_is_bounded_and_keeps_last_good_values(paths, code, message):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    source = {"code": code, "fields": {"summary": {"type": "string"}}}
    env = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}
    with TestClient(create_app(make_manager(paths, persist=True, env=env))) as client:
        path = upload(client, pid, source=source)
        request = {"path": path, "source_id": "root"}
        version = client.post(api(pid, "/card-sources/refresh"), json=request).json()["events"][0][
            "data"
        ]["code_version"]
        assert (
            client.post(
                api(pid, "/card-sources/approval"),
                json={**request, "code_version": version, "approved": True},
            ).status_code
            == 200
        )
        result = client.post(api(pid, "/card-sources/refresh"), json=request).json()
        assert result["status"] == "error"
        assert message in result["events"][0]["data"]["error"]
        assert result["events"][0]["data"]["data"]["summary"] == "Last good"


def test_legacy_source_less_saved_files_remain_passive(paths):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    legacy = document()
    legacy["message"]["data"].pop("_sources")
    with TestClient(create_app(make_manager(paths, persist=True))) as client:
        assert (
            client.post(
                api(pid, "/files/upload"),
                files={"files": ("legacy.card-instance.yaml", yaml.safe_dump(legacy))},
            ).status_code
            == 200
        )
        assert client.get(
            api(pid, "/card-instances"), params={"path": "legacy.card-instance.yaml"}
        ).json()["message"]["data"] == {"summary": "Last good", "unrelated": "Keep"}
        assert (
            client.post(
                api(pid, "/card-sources/refresh"),
                json={"path": "legacy.card-instance.yaml", "source_id": "root"},
            ).status_code
            == 404
        )


@pytest.mark.parametrize("case", ["result", "invalid", "error"])
def test_selected_secrets_are_redacted_after_json_decoding_and_before_error_truncation(paths, case):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    secret_value = 'secret-\n"\\😀value'

    async def execute(config, source, arguments, keys):
        value = keys["DATA_KEY"]
        if case == "error":
            raise ValueError("x" * 490 + value)
        return json.dumps({"summary": value, "nested": {value: [value]}})

    fields = {"summary": {"type": "string"}, "nested": {"type": "object"}}
    if case == "invalid":
        fields["summary"]["enum"] = ["allowed"]
    source = {"code": "print('{}')", "secret_names": ["DATA_KEY"], "fields": fields}
    with TestClient(
        create_app(make_manager(paths, persist=True), card_source_executor=execute)
    ) as client:
        path = upload(client, pid, source=source)
        request = {"path": path, "source_id": "root"}
        version = client.post(api(pid, "/card-sources/refresh"), json=request).json()["events"][0][
            "data"
        ]["code_version"]
        identity = client.post(
            "/api/secrets", json={"name": "Source key", "value": secret_value}
        ).json()["secret"]["id"]
        assert (
            client.post(
                api(pid, "/card-sources/approval"),
                json={
                    **request,
                    "code_version": version,
                    "approved": True,
                    "secrets": {"DATA_KEY": identity},
                },
            ).status_code
            == 200
        )
        response = client.post(api(pid, "/card-sources/refresh"), json=request)
        result = response.json()
        assert "secret-" not in json.dumps(result), response.text
        if case == "result":
            assert result["status"] == "updated"
            data = result["events"][0]["data"]["data"]
            assert data["summary"] == "[redacted]"
            assert data["nested"] == {"[redacted]": ["[redacted]"]}
            opened = client.get(api(pid, "/card-instances"), params={"path": path}).json()
            assert "secret-" not in json.dumps(opened)
        else:
            assert result["status"] == "error"
            assert result["events"][0]["data"]["data"]["summary"] == "Last good"


def test_quotes_generation_requires_chosen_parameters_and_retains_its_refresh_source(paths):
    pid = ProfileRegistry(paths).create_profile("Sources", "#109e91").id
    fields = {
        "title": "Chosen quotes",
        "quotes": [{"symbol": "AAPL", "name": "Apple", "price": 100, "changePercent": 0}],
    }
    calls = [fields, {**fields, "_parameters": {"symbols": "AAPL", "title": "Chosen quotes"}}]
    models = ScriptedModels(
        lambda cfg, model: [
            *[
                ToolCallEvent(
                    name="run_skill_script",
                    arguments=json.dumps(
                        {
                            "name": "rich-views",
                            "script": "MarketBoard",
                            "args": args,
                        }
                    ),
                )
                for args in calls
            ],
            "done",
        ]
    )

    async def quotes(**args):
        assert args == {"symbols": "AAPL", "title": "Chosen quotes"}
        return json.dumps(
            {
                **fields,
                "quotes": [{"symbol": "AAPL", "name": "Apple", "price": 101, "changePercent": 1}],
            }
        )

    with TestClient(
        create_app(
            make_manager(paths, persist=True, model_factory=models),
            card_source_tools={"get_quotes": quotes},
        )
    ) as client:
        send(client, pid)
        surfaces = [e["data"] for e in replay(client, pid) if e["type"].endswith("A2UISurface")]
        assert len(surfaces) == 1
        surface = surfaces[0]
        assert surface["data"]["_sources"]["root"]["parameters"] == calls[1]["_parameters"]
        count = len(models.requests)
        result = client.post(
            api(pid, "/card-sources/refresh"),
            json={
                "chat_id": "drafts",
                "surface_id": surface["surface_id"],
                "source_id": "root",
            },
        ).json()
        assert result["status"] == "updated"
        assert result["events"][0]["data"]["data"]["quotes"][0]["price"] == 101
        assert len(models.requests) == count
