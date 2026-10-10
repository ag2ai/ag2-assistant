"""Card authoring through real Gateway turns and structured Skill calls."""

import asyncio
import json

import pytest
import yaml
from ag2.events import ToolCallEvent, ToolErrorEvent, ToolResultEvent
from ag2.knowledge import SqliteKnowledgeStore
from ag2.knowledge.log import EventLogWriter

from assistant.a2ui import CardCatalog
from assistant.card_drafts import DraftError
from assistant.cards import CARDS_DIR
from assistant.events import A2UISurface, EphemeralCard
from assistant.skills import SkillStateStore
from tests.support.apps import real_gateway
from tests.support.cards import card_definition as definition
from tests.support.fakes import ControlledHistory, ScriptedModels


def call(script, args):
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


async def drafts(gateway, chat="drafts"):
    stream = await gateway.stream_for(chat)
    return [
        event for event in await stream.history.get_events() if isinstance(event, EphemeralCard)
    ]


async def test_unknown_card_is_drawn_and_durable_with_rich_views_off(config):
    SkillStateStore(config.root_dir).set_enabled("rich-views", False)
    models = ScriptedModels(
        lambda cfg, model: [
            call("draft_card", {"definition": definition(), "data": {"title": "My books"}}),
            "done",
        ]
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    catalog = CardCatalog(config).cards()
    try:
        await gateway.send_message("Draw my shelf", chat_id="drafts")
        events = await drafts(gateway)
        history = await (await gateway.stream_for("drafts")).history.get_events()
        assert len(events) == 1, "\n".join(
            p.content
            for e in history
            if isinstance(e, (ToolErrorEvent, ToolResultEvent))
            for p in e.result.parts
        )
        event = events[0]
        assert event.data == {"title": "My books"}
        assert event.definition["example"] == {"title": "Books"}
        assert event.component["component"] == "Card"
        assert not (config.workspace_dir / CARDS_DIR).exists()
        assert CardCatalog(config).cards() == catalog
    finally:
        await gateway.close()
    changed = definition()
    changed["layout"][1]["text"] = "Changed catalog definition"
    directory = config.workspace_dir / CARDS_DIR
    directory.mkdir(parents=True)
    (directory / "shelf.card.yaml").write_text(yaml.safe_dump(changed))
    restarted = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await restarted.start()
    try:
        replay = await drafts(restarted)
        assert [entry.to_dict() for entry in replay] == [entry.to_dict() for entry in events]
    finally:
        await restarted.close()


async def test_cancelling_after_authoring_preserves_the_durable_version(config):
    entered, release = asyncio.Event(), asyncio.Event()

    async def before_call(messages, context):
        if len(models.requests) == 2:
            entered.set()
            await release.wait()

    models = ScriptedModels(
        lambda cfg, model: [
            call("draft_card", {"definition": definition(), "data": {"title": "Committed"}}),
            "done",
        ],
        before_call=before_call,
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    try:
        turn = asyncio.create_task(gateway.send_message("Draw", chat_id="drafts"))
        await asyncio.wait_for(entered.wait(), 5)
        before = [event.to_dict() for event in await drafts(gateway)]
        assert len(before) == 1
        assert await gateway.cancel_turn("drafts")
        await asyncio.gather(turn, return_exceptions=True)
    finally:
        release.set()
        await gateway.close()
    restarted = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await restarted.start()
    try:
        assert [event.to_dict() for event in await drafts(restarted)] == before
        event = (await drafts(restarted))[0]
        stream = await restarted.stream_for("drafts")
        store = SqliteKnowledgeStore(str(config.data_dir / "chats.db"))
        try:
            await EventLogWriter(store).persist_dropped(
                stream.id, await stream.history.get_events()
            )
        finally:
            store.close()
        await stream.history.replace([])
        models.script = lambda cfg, model: [
            call("read_draft", {"draft_id": event.draft_id, "version": 1}),
            call(
                "save_card",
                {
                    "draft_id": event.draft_id,
                    "expected_version": 1,
                    "surface_id": event.surface_id,
                    "name": "Shelf",
                    "filename": "shelf.card.yaml",
                    "request_id": "after-cancellation",
                },
            ),
            "done",
        ]
        await restarted.send_message("Read and save the committed Card", chat_id="drafts")
        path = config.workspace_dir / CARDS_DIR / "shelf.card.yaml"
        assert yaml.safe_load(path.read_text()) == definition()
        history = await (await restarted.stream_for("drafts")).history.get_events()
        results = [e.result.parts[0].content for e in history if isinstance(e, ToolResultEvent)]
        assert json.loads(results[-2])["data"] == {"title": "Committed"}
    finally:
        await restarted.close()
    reopened = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await reopened.start()
    try:
        assert [event.to_dict() for event in await drafts(reopened)] == before
    finally:
        await reopened.close()


async def test_invalid_definition_and_data_can_be_repaired_in_the_same_turn(config):
    invalid = definition()
    invalid["layout"][1]["text"] = {"path": "./title"}
    program = [
        call("draft_card", {"definition": invalid, "data": {"title": "Real"}}),
        call("draft_card", {"definition": definition(), "data": {"title": 17}}),
        call("draft_card", {"definition": definition(), "data": {"title": "Repaired"}}),
        "done",
    ]
    gateway = real_gateway(
        config,
        memory=False,
        onboard=False,
        model_factory=ScriptedModels(lambda cfg, model: program),
    )
    await gateway.start()
    try:
        await gateway.send_message("Draw and repair", chat_id="drafts")
        events = await drafts(gateway)
        assert len(events) == 1
        assert events[0].draft_version == 1 and events[0].data == {"title": "Repaired"}
        history = await (await gateway.stream_for("drafts")).history.get_events()
        errors = [e.result.parts[0].content for e in history if isinstance(e, ToolResultEvent)]
        assert "relative binding" in errors[0]
        assert "data title" in errors[1]
        assert not (config.workspace_dir / CARDS_DIR).exists()
    finally:
        await gateway.close()


async def test_explicit_save_keeps_the_example_separate_and_requires_replace(config):
    models = ScriptedModels(
        lambda cfg, model: [
            call("draft_card", {"definition": definition(), "data": {"title": "Current books"}}),
            "done",
        ]
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    try:
        await gateway.send_message("Draw", chat_id="drafts")
        event = (await drafts(gateway))[0]
        request = dict(
            draft_id=event.draft_id,
            expected_version=1,
            surface_id=event.surface_id,
            name="Shelf",
            filename="shelf.card.yaml",
            request_id="save-1",
        )
        saved = await gateway.save_card_definition("drafts", **request)
        assert saved["status"] == "saved"
        path = config.workspace_dir / "cards" / "shelf.card.yaml"
        assert yaml.safe_load(path.read_text()) == definition()
        assert await gateway.save_card_definition("drafts", **request) == saved
        conflict = await gateway.save_card_definition(
            "drafts", **{**request, "request_id": "save-2"}
        )
        assert conflict["status"] == "conflict"
        replaced = await gateway.save_card_definition(
            "drafts",
            **{
                **request,
                "request_id": "save-2",
                "replace": True,
                "conflict_token": conflict["conflict_token"],
            },
        )
        assert replaced["status"] == "saved"
        assert len(list(path.parent.glob("*.card.yaml"))) == 1
        models.script = lambda cfg, model: [
            call(
                "save_card",
                {
                    **request,
                    "name": "Shelf copy",
                    "filename": "shelf-copy.card.yaml",
                    "request_id": "conversational-copy",
                },
            ),
            "done",
        ]
        await gateway.send_message("Save this Card as Shelf copy", chat_id="drafts")
        copied = config.workspace_dir / "cards" / "shelf-copy.card.yaml"
        assert yaml.safe_load(copied.read_text()) == {**definition(), "name": "Shelf copy"}
    finally:
        await gateway.close()


async def test_independent_drafts_revise_restore_and_reuse_after_restart(config):
    program = [
        call("draft_card", {"definition": definition("Graph"), "data": {"title": "Graph one"}}),
        call(
            "draft_card", {"definition": definition("Calendar"), "data": {"title": "Calendar one"}}
        ),
        "done",
    ]
    models = ScriptedModels(lambda cfg, model: program)
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    try:
        await gateway.send_message("Draw two", chat_id="drafts")
        graph, calendar = await drafts(gateway)
        program[:] = [
            call(
                "draft_card",
                {
                    "definition": definition("Graph"),
                    "data": {"title": "Graph two"},
                    "draft_id": graph.draft_id,
                    "expected_version": 1,
                },
            ),
            call(
                "draft_card",
                {
                    "definition": definition("Calendar"),
                    "data": {"title": "Calendar two"},
                    "draft_id": calendar.draft_id,
                    "expected_version": 1,
                },
            ),
            "done",
        ]
        await gateway.send_message("Revise each", chat_id="drafts")
        assert [(e.definition["name"], e.draft_version) for e in await drafts(gateway)] == [
            ("Graph", 1),
            ("Calendar", 1),
            ("Graph", 2),
            ("Calendar", 2),
        ]
    finally:
        await gateway.close()

    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    try:
        program[:] = [call("read_draft", {"draft_id": graph.draft_id, "version": 1}), "done"]
        await gateway.send_message("Read old graph", chat_id="drafts")
        history = await (await gateway.stream_for("drafts")).history.get_events()
        results = [e.result.parts[0].content for e in history if isinstance(e, ToolResultEvent)]
        assert json.loads(results[-1])["data"] == {"title": "Graph one"}
        program[:] = [
            call("restore_card", {"draft_id": graph.draft_id, "version": 1, "expected_version": 2}),
            "done",
        ]
        await gateway.send_message("Restore old graph", chat_id="drafts")
        events = await drafts(gateway)
        assert events[-1].draft_id == graph.draft_id
        assert events[-1].draft_version == 3
        assert events[-1].data == {"title": "Graph one"}
        assert len({e.surface_id for e in events}) == 5
        request = dict(
            draft_id=graph.draft_id,
            expected_version=1,
            surface_id=graph.surface_id,
            name="Graph",
            filename="graph.card.yaml",
            request_id="stale",
        )
        with pytest.raises(DraftError, match="Obsolete"):
            await gateway.save_card_definition("drafts", **request)
        with pytest.raises(DraftError, match="not found"):
            await gateway.save_card_definition("another-chat", **request)
        saved = await gateway.save_card_definition(
            "drafts",
            **{
                **request,
                "expected_version": 3,
                "surface_id": events[-1].surface_id,
                "request_id": "current",
            },
        )
        assert saved["status"] == "saved"
        assert not (config.workspace_dir / "cards" / "calendar.card.yaml").exists()
        program[:] = [
            ToolCallEvent(
                name="run_skill_script",
                arguments=json.dumps(
                    {
                        "name": "rich-views",
                        "script": "Graph",
                        "args": {"title": "Reused"},
                    }
                ),
            ),
            "done",
        ]
        await gateway.send_message("Reuse saved graph", chat_id="another-chat")
        history = await (await gateway.stream_for("another-chat")).history.get_events()
        assert any(isinstance(e, A2UISurface) and e.data == {"title": "Reused"} for e in history)
        assert await gateway.delete_chat("drafts")
        assert (config.workspace_dir / "cards" / "graph.card.yaml").is_file()
    finally:
        await gateway.close()


async def test_history_failure_draws_nothing_and_a_committed_file_can_be_reconciled(config):
    stores = []

    def factory(path):
        store = ControlledHistory(path)
        stores.append(store)
        return store

    models = ScriptedModels(
        lambda cfg, model: [
            call("draft_card", {"definition": definition(), "data": {"title": "Current"}}),
            "done",
        ]
    )
    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=models, history_factory=factory
    )
    await gateway.start()
    try:
        stores[0].fail = True
        await gateway.send_message("Draw", chat_id="drafts")
        assert await drafts(gateway) == []
        assert not (config.workspace_dir / "cards").exists()
        stores[0].fail = False
        await gateway.send_message("Repair", chat_id="drafts")
        event = (await drafts(gateway))[0]
        request = dict(
            draft_id=event.draft_id,
            expected_version=1,
            surface_id=event.surface_id,
            name="Shelf",
            filename="shelf.card.yaml",
            request_id="lost-response",
        )
        stores[0].saved_status_fails = True
        saved = await gateway.save_card_definition("drafts", **request)
        assert saved["status"] == "saved"
        assert not saved["history_recorded"]
        assert "Saved cards/shelf.card.yaml" in saved["message"]
        path = config.workspace_dir / "cards" / "shelf.card.yaml"
        assert yaml.safe_load(path.read_text()) == definition()
        stores[0].saved_status_fails = False
        retry = await gateway.save_card_definition("drafts", **request)
        assert retry["history_recorded"]
        assert len(list(path.parent.glob("*.card.yaml"))) == 1
    finally:
        await gateway.close()


async def test_changed_destination_and_failed_atomic_replace_preserve_the_previous_file(config):
    models = ScriptedModels(
        lambda cfg, model: [
            call("draft_card", {"definition": definition(), "data": {"title": "Current"}}),
            "done",
        ]
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    try:
        await gateway.send_message("Draw", chat_id="drafts")
        event = (await drafts(gateway))[0]
        request = dict(
            draft_id=event.draft_id,
            expected_version=1,
            surface_id=event.surface_id,
            name="Shelf",
            filename="shelf.card.yaml",
            request_id="first",
        )
        await gateway.save_card_definition("drafts", **request)
        request["request_id"] = "replace"
        first = await gateway.save_card_definition("drafts", **request)
        path = config.workspace_dir / "cards" / "shelf.card.yaml"
        changed = path.read_text() + "\n# Edited by the user\n"
        path.write_text(changed)
        second = await gateway.save_card_definition(
            "drafts",
            **{
                **request,
                "replace": True,
                "conflict_token": first["conflict_token"],
            },
        )
        assert second["status"] == "conflict"
        assert second["conflict_token"] != first["conflict_token"]
        assert path.read_text() == changed
        path.parent.chmod(0o555)
        try:
            with pytest.raises(OSError, match="previous file preserved"):
                await gateway.save_card_definition(
                    "drafts",
                    **{
                        **request,
                        "replace": True,
                        "conflict_token": second["conflict_token"],
                    },
                )
            assert path.read_text() == changed
        finally:
            path.parent.chmod(0o755)
        copied = await gateway.save_card_definition(
            "drafts",
            **{
                **request,
                "name": "Shelf copy",
                "filename": "shelf-copy.card.yaml",
                "request_id": "copy",
            },
        )
        assert copied["status"] == "saved"
        assert len(list(path.parent.glob("*.card.yaml"))) == 2
    finally:
        await gateway.close()


async def test_save_and_revision_use_commit_order_without_waiting_for_the_turn(config):
    entered, release = asyncio.Event(), asyncio.Event()
    block = False

    async def before_call(messages, context):
        if block:
            entered.set()
            await release.wait()

    program = [call("draft_card", {"definition": definition(), "data": {"title": "One"}}), "done"]
    models = ScriptedModels(lambda cfg, model: program, before_call=before_call)
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    try:
        await gateway.send_message("Draw", chat_id="drafts")
        event = (await drafts(gateway))[0]
        revised = {**definition(), "description": "Revised books"}
        program[:] = [
            call(
                "draft_card",
                {
                    "definition": revised,
                    "data": {"title": "Two"},
                    "draft_id": event.draft_id,
                    "expected_version": 1,
                },
            ),
            "done",
        ]
        block = True
        turn = asyncio.create_task(gateway.send_message("Revise", chat_id="drafts"))
        await entered.wait()
        request = dict(
            draft_id=event.draft_id,
            expected_version=1,
            surface_id=event.surface_id,
            name="Shelf",
            filename="shelf.card.yaml",
            request_id="before-revision",
        )
        saved = await asyncio.wait_for(gateway.save_card_definition("drafts", **request), 2)
        assert saved["status"] == "saved"
        release.set()
        await turn
        assert (await drafts(gateway))[-1].draft_version == 2
        assert (
            yaml.safe_load((config.workspace_dir / "cards" / "shelf.card.yaml").read_text())
            == definition()
        )
        with pytest.raises(DraftError, match="Obsolete"):
            await gateway.save_card_definition(
                "drafts", **{**request, "request_id": "after-revision"}
            )
    finally:
        release.set()
        await gateway.close()
