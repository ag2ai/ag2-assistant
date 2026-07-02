"""The one wire contract: an event ⇄ ``{type, data}``.

This is the same representation ``EventLogWriter`` persists, so persist, replay,
and live-stream share one shape. The gateway forwards `to_wire(event)` over the
WebSocket; the client renders by ``type`` and reloads history by replaying the
exact same records. Custom ``ag2assistant.events.*`` round-trip because deserialization
resolves the class by its fully-qualified name (dynamic import).

Audio events travel as raw binary frames, not JSON — `is_binary_event` flags them.
"""

from ag2.events._serialization import qualified_name
from ag2.events.voice import RecordedAudioEvent, SynthesizedAudioEvent

# Events that travel as raw PCM binary on their own frame, never as {type, data}.
_BINARY = (SynthesizedAudioEvent, RecordedAudioEvent)


def is_binary_event(event) -> bool:
    """True for audio events that should be sent as a binary frame, not JSON."""
    return isinstance(event, _BINARY)


def to_wire(event) -> dict:
    """Serialize any AG2 event to the wire/log shape ``{type, data}``."""
    return {"type": qualified_name(event), "data": event.to_dict()}
