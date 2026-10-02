"""The one wire contract: an event ⇄ ``{type, data}``.

This is the same representation ``EventLogWriter`` persists, so persist, replay,
and live-stream share one shape. The gateway forwards `to_wire(event)` over the
WebSocket; the client renders by ``type`` and reloads history by replaying the
exact same records. Custom ``ag2assistant.events.*`` round-trip because deserialization
resolves the class by its fully-qualified name (dynamic import).

Audio events travel as raw binary frames, not JSON — `is_binary_event` flags them.

What is *persisted* is `to_wire(event)`; what is *sent to a client* is
`to_wire(as_drawn(event))`, so a surface holding a Card instance — the server-filled
coding session, or one stored before that Card was a file — is drawn on every read.
"""

from ag2.events._serialization import qualified_name
from ag2.events.voice import RecordedAudioEvent, SynthesizedAudioEvent

from assistant.a2ui import CardCatalog, expanded_card_surface
from assistant.coding.surface import adopted_surface
from assistant.events import A2UISurface
from assistant.observability import log_suppressed

# Events that travel as raw PCM binary on their own frame, never as {type, data}.
_BINARY = (SynthesizedAudioEvent, RecordedAudioEvent)


def is_binary_event(event) -> bool:
    """True for audio events that should be sent as a binary frame, not JSON."""
    return isinstance(event, _BINARY)


def to_wire(event) -> dict:
    """Serialize any AG2 event to the wire/log shape ``{type, data}``."""
    return {"type": qualified_name(event), "data": event.to_dict()}


def as_drawn(event, catalog: CardCatalog):
    """One event as a client should be sent it: an A2UI surface carrying a Card
    instance comes back as the primitives ``catalog``'s layout for it declares."""
    if not isinstance(event, A2UISurface):
        return event
    try:
        return expanded_card_surface(adopted_surface(event), catalog.drawable())
    except Exception as exc:  # noqa: BLE001 — a Card that won't draw isn't a dead turn
        log_suppressed("a2ui card expansion", exc, surface_id=event.surface_id)
        return event
