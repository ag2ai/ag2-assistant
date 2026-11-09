"""A channel turn's A2UI Cards reach the adapter that asks for them, and a tapped
card button runs as a turn in that Peer's Chat."""

import json

from ag2.a2ui.incoming import A2UIIncomingAction
from ag2.events import ToolCallEvent

from assistant.channels.base import InboundMessage
from assistant.channels.router import Redraw, Reply
from assistant.events import A2UISurface
from assistant.pairing import PairingStore
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager
from tests.support.fakes import ScriptedModels

WEATHER = {
    "location": "Vienna, Austria",
    "condition": "sunny",
    "temperature": "24°",
    "rows": [{"label": "Wind", "value": "12 km/h NW"}],
}
DRAW_WEATHER = ToolCallEvent(
    name="run_skill_script",
    arguments=json.dumps({"name": "rich-views", "script": "WeatherPanel", "args": WEATHER}),
)


def _message(text: str = "what is on my shelf?") -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id="1001",
        chat_id="chat",
        platform="telegram",
        connection="telegram",
        is_direct=True,
        mentioned=True,
    )


async def _manager(paths, models):
    registry = ProfileRegistry(paths)
    profile = registry.create_profile("Channel", "#109e91")
    registry.set_connection_default("telegram", profile.id)
    PairingStore(paths).add_account("telegram", "1001", platform="telegram")
    manager = make_manager(paths, persist=True, model_factory=models)
    await manager.start()
    return manager


async def test_a_turn_hands_its_drawn_cards_to_an_adapter_that_asks(paths):
    models = ScriptedModels(script=lambda cfg, model: [DRAW_WEATHER, "Here it is."])
    manager = await _manager(paths, models)
    try:
        reply = await manager.router.handle(_message(), cards=True)

        assert isinstance(reply, Reply) and reply.text == "Here it is."
        (surface,) = reply.surfaces
        assert isinstance(surface, A2UISurface)
        # Drawn as primitives, the way the web client is sent it — not as the Card name.
        kinds = {c.get("component") for c in surface.component["_components"]}
        assert "WeatherPanel" not in kinds and "WeatherGlyph" in kinds
        assert surface.data["location"] == "Vienna, Austria"
    finally:
        await manager.close()


async def test_an_adapter_that_does_not_ask_gets_only_the_text(paths):
    models = ScriptedModels(script=lambda cfg, model: [DRAW_WEATHER, "Here it is."])
    manager = await _manager(paths, models)
    try:
        assert await manager.router.handle(_message()) == Reply("Here it is.")
    finally:
        await manager.close()


async def test_a_tapped_card_button_runs_a_turn_naming_the_action(paths):
    models = ScriptedModels()
    manager = await _manager(paths, models)
    tap = A2UIIncomingAction(
        name="book",
        surface_id="s1",
        source_component_id="yes",
        timestamp="2026-11-08T10:00:00+00:00",
        context={"time": "19:00"},
    )
    try:
        assert await manager.router.act(_message(""), tap, cards=True) == Reply("done")

        asked = " ".join(
            str(getattr(part, "content", "")) for m in models.requests[-1][3] for part in m.parts
        )
        assert "[[A2UI_ACTION]]" in asked and "'book'" in asked and "19:00" in asked
    finally:
        await manager.close()


async def test_a_tapped_server_action_hands_back_the_data_it_wrote(paths):
    models = ScriptedModels()
    manager = await _manager(paths, models)
    tap = A2UIIncomingAction(
        name="save_surface",
        surface_id="s1",
        source_component_id="save",
        timestamp="2026-11-08T10:00:00+00:00",
        context={"data": {"status": "booked"}},
    )
    try:
        outcome = await manager.router.act(_message(""), tap, cards=True)

        assert outcome == Redraw("s1", {"status": "booked"})
        assert models.requests == []
    finally:
        await manager.close()
