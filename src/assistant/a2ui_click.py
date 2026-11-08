"""A click on an A2UI surface, from any client: a registered server action runs here,
and any other Button action becomes the text of a new Turn."""

import json
from dataclasses import dataclass, replace
from typing import Any

from ag2.a2ui.incoming import A2UIIncomingAction, A2UIIncomingActionResult, parse_incoming_message
from ag2.a2ui.server_action import build_server_action_context, run_server_action

from assistant.a2ui import A2UI_SERVER_ACTIONS
from assistant.events import A2UIActionSubmitted, A2UISurfaceDataUpdated

# Cap on the data model a click carries, bounding any client-supplied body.
MAX_A2UI_STATE_BYTES = 256 * 1024


class InvalidClick(ValueError):
    """The envelope is not an A2UI action this app can handle."""


@dataclass(frozen=True)
class ClickOutcome:
    """What a click did: the Turn text to run, or the data model a server action wrote,
    and whether the client's data model was refused as too large."""

    turn_text: str | None
    data: dict | None = None
    oversized: bool = False


def parse_click(message: Any) -> A2UIIncomingAction:
    """The action in one client-to-server A2UI envelope, or `InvalidClick`."""
    parsed = parse_incoming_message(message)
    if not isinstance(parsed, A2UIIncomingActionResult):
        raise InvalidClick("Invalid A2UI action.")
    if not parsed.action.surface_id or not parsed.action.source_component_id:
        raise InvalidClick("Unsupported A2UI action.")
    return parsed.action


def _clicked_instance_model(state: Any, surface_id: str) -> dict | None:
    """The data model a click sends for the Card instance `surface_id` — None when it
    sends none, or names a different instance."""
    if not isinstance(state, dict) or state.get("surfaceId") != surface_id:
        return None
    data = state.get("data")
    return data if isinstance(data, dict) else None


def _oversized_model(model: dict) -> bool:
    """Whether a client-supplied data model exceeds the bound on a persisted body."""
    try:
        return len(json.dumps(model, ensure_ascii=False).encode()) > MAX_A2UI_STATE_BYTES
    except (TypeError, ValueError):
        return True


async def submit_click(
    gateway, chat_id: str, click: A2UIIncomingAction, state: Any = None, version: Any = "v1.0"
) -> ClickOutcome:
    """Apply one clicked action to `chat_id`'s stream."""
    action = A2UI_SERVER_ACTIONS.get(click.name)
    if action is None:
        model = _clicked_instance_model(state, click.surface_id)
        oversized = model is not None and _oversized_model(model)
        if model is not None and not oversized:
            await gateway.emit_event(chat_id, A2UISurfaceDataUpdated(click.surface_id, data=model))
        await gateway.emit_event(
            chat_id, A2UIActionSubmitted(click.surface_id, action_name=click.name)
        )
        text = (
            f"[[A2UI_ACTION]] The user clicked the A2UI action '{click.name}' on surface "
            f"'{click.surface_id}'. Its current values are: {click.context}. "
            "Carry out the requested action and respond to the user."
        )
        return ClickOutcome(text, oversized=oversized)
    scoped = replace(click, context={**click.context, "surface_id": click.surface_id})
    messages = await run_server_action(
        action,
        scoped,
        version=version,
        context=build_server_action_context(gateway.require_agent()),
    )
    written = None
    for reply in messages:
        update = reply.get("updateDataModel")
        if isinstance(update, dict) and update.get("surfaceId") == click.surface_id:
            value = update.get("value")
            if update.get("path", "/") == "/" and isinstance(value, dict):
                await gateway.emit_event(
                    chat_id, A2UISurfaceDataUpdated(click.surface_id, data=value)
                )
                written = value
    return ClickOutcome(None, data=written)
