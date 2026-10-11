"""The event bridge replays history, forwards live events as {type,data}, skips
binary audio, and runs turns through the gateway."""

from ag2.events import ModelMessage, ModelRequest, ModelResponse
from ag2.events.voice import SynthesizedAudioEvent

from assistant.events import TaskCreated
from assistant.gateway.history import event_cursor
from assistant.gateway.stream_bridge import StreamBridge


class _WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


class _Stream:
    def __init__(self, events):
        self._events = events
        self.history = self
        self.sub = None
        self.unsubbed = None

    async def get_events(self):
        return list(self._events)

    def subscribe(self, fn):
        self.sub = fn
        return "sub-1"

    def unsubscribe(self, sid):
        self.unsubbed = sid


class _Catalog:
    """Stands in for the profile's CardCatalog: this bridge draws no Card."""

    def drawable(self):
        return {}


class _GW:
    def __init__(self, stream):
        self._stream = stream
        self.turns = []

    async def stream_for(self, sid):
        return self._stream

    async def replay_events(self, sid):
        return await self._stream.history.get_events()

    def is_running(self, sid):
        return False

    catalog = _Catalog()
    card_sources = None

    async def send_message(
        self,
        text,
        chat_id=None,
        asker=None,
        attachments=None,
        surface="",
        attachment_names=(),
        chat_model="",
    ):
        self.turns.append((text, chat_id))
        return "ok"


def _events(ws):
    return [m for m in ws.sent if "event" in m]


async def test_bridge_replays_then_forwards_and_runs_turns():
    stream = _Stream([TaskCreated("task-1", title="X")])
    gw, ws = _GW(stream), _WS()
    bridge = StreamBridge(gw, ws, "s1")

    await bridge.open()
    assert _events(ws)[0]["event"]["type"].endswith("TaskCreated")  # replayed history
    assert any(m.get("type") == "ready" for m in ws.sent)
    assert stream.sub is not None  # subscribed for live

    await stream.sub(TaskCreated("task-2", title="Y"))  # a live event
    assert len(_events(ws)) == 2

    await bridge.run_turn("hello", asker=None)
    assert gw.turns == [("hello", "s1")]
    assert any(m.get("type") == "turn_end" for m in ws.sent)

    bridge.close()
    assert stream.unsubbed == "sub-1"


async def test_bridge_skips_binary_audio_events():

    stream = _Stream([])
    bridge = StreamBridge(_GW(stream), (ws := _WS()), "s1")
    await bridge.open()
    await stream.sub(SynthesizedAudioEvent(b"\x00\x01"))
    assert _events(ws) == []  # audio never forwarded as {type,data}


async def test_paginated_bridge_replays_complete_turns_and_forwards_events_during_replay():
    history = []
    for index in range(6):
        history.extend(
            [
                ModelRequest.ensure_request([f"question {index}"]),
                ModelResponse(ModelMessage(f"reply {index}")),
            ]
        )
    stream = _Stream(history)
    live = TaskCreated("during-replay", title="During replay")

    class RacingSocket(_WS):
        async def send_json(self, obj):
            self.sent.append(obj)
            if len(self.sent) == 1:
                await stream.sub(history[-1])
                await stream.sub(live)

    ws = RacingSocket()
    bridge = StreamBridge(_GW(stream), ws, "s1", history_limit=2)
    await bridge.open()
    bridge.close()
    assert [frame["event"]["data"] for frame in _events(ws)] == [
        event.to_dict() for event in [*history[-4:], live]
    ]
    ready_index = next(i for i, frame in enumerate(ws.sent) if frame.get("type") == "ready")
    assert ready_index == 4
    assert ws.sent[ready_index]["history"] == {
        "before": event_cursor(history[-4]),
        "start": event_cursor(history[-4]),
    }
    assert ws.sent[ready_index]["busy"] is False


async def test_reconnect_replays_the_previously_loaded_range():
    events = [ModelRequest.ensure_request([str(index)]) for index in range(8)]
    ws = _WS()
    bridge = StreamBridge(
        _GW(_Stream(events)), ws, "s1", history_limit=2, history_from=event_cursor(events[2])
    )
    await bridge.open()
    bridge.close()
    assert [frame["event"]["data"] for frame in _events(ws)] == [
        event.to_dict() for event in events[2:]
    ]
