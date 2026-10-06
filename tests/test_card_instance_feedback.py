"""Save receipts survive a failed durable-feedback write."""

from assistant.events import CardInstanceSaved, EphemeralCard
from tests.support.apps import real_gateway
from tests.support.cards import author, card_definition
from tests.support.fakes import ControlledHistory, ScriptedModels


async def test_a_saved_file_is_reported_truthfully_and_retry_finishes_feedback(config):
    stores = []

    def history_factory(path):
        store = ControlledHistory(path)
        stores.append(store)
        return store

    models = ScriptedModels(
        lambda cfg, model: [
            author("draft_card", {"definition": card_definition(), "data": {"title": "Retain"}}),
            "done",
        ]
    )
    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=models, history_factory=history_factory
    )
    await gateway.start()
    try:
        await gateway.send_message("Draw", chat_id="drafts")
        stream = await gateway.stream_for("drafts")
        source = next(
            event for event in await stream.history.get_events() if isinstance(event, EphemeralCard)
        )
        message = {
            key: source.to_dict()[key]
            for key in ("version", "catalog_id", "component", "data", "title", "intent")
        }
        request = {
            "surface_id": source.surface_id,
            "message": message,
            "path": "copy.card-instance.yaml",
            "request_id": "lost-response",
        }
        stores[0].fail = True
        saved = await gateway.save_card_instance("drafts", **request)
        assert saved["status"] == "saved" and not saved["history_recorded"]
        assert "Saved to copy.card-instance.yaml" in saved["message"]
        contents = (config.workspace_dir / saved["path"]).read_bytes()
        assert not any(
            isinstance(event, CardInstanceSaved) for event in await stream.history.get_events()
        )
        stores[0].fail = False
        result = await gateway.save_card_instance("drafts", **request)
        assert result["history_recorded"]
        assert result["instance_id"] == saved["instance_id"]
        assert await gateway.save_card_instance("drafts", **request) == result
        assert (config.workspace_dir / saved["path"]).read_bytes() == contents
        notices = [
            event
            for event in await stream.history.get_events()
            if isinstance(event, CardInstanceSaved)
        ]
        assert len(notices) == 1 and notices[0].path == "copy.card-instance.yaml"
    finally:
        stores[0].fail = False
        await gateway.close()
