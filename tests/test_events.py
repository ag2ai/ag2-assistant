"""AG2 Assistant custom events round-trip through the AG2 serialize/persist contract.

The whole GUI-redesign rests on this: app events serialize to `{type, data}`,
persist via EventLogWriter, and reload by dynamic class import — exactly like
native AG2 events. If these break, history/replay break.
"""

import pytest
from ag2.knowledge.log import import_event_class

from assistant.events import (
    DeliverableProduced,
    InquiryAnswered,
    InquiryRaised,
    TaskCreated,
    TaskScheduled,
)
from assistant.gateway.wire import is_binary_event, to_wire

_SAMPLES = [
    TaskCreated("task-1", title="Weather report", kind="scheduled"),
    TaskScheduled("task-1", scheduled_for="2026-06-18T08:00:00+10:00", recurrence="daily"),
    DeliverableProduced(
        "task-1", deliverable_id="dlv-9", description="report", preview="RBA held rates…"
    ),
    InquiryRaised(
        "inq-1",
        task_id="task-1",
        question="Which city?",
        options=["Sydney", "Perth"],
        kind="question",
    ),
    InquiryAnswered("inq-1", answer="Sydney"),
]


@pytest.mark.parametrize("event", _SAMPLES, ids=lambda e: type(e).__name__)
def test_custom_event_round_trips_through_wire(event):
    record = to_wire(event)
    assert set(record) == {"type", "data"}
    assert record["type"].startswith("assistant.events.")

    cls = import_event_class(record["type"])  # the deserializer's resolution path
    assert cls is type(event)

    back = cls.from_dict(record["data"])
    assert type(back) is type(event)
    # every declared field survives the round-trip
    for f in event._event_fields_:
        assert getattr(back, f) == getattr(event, f)


def test_audio_events_flagged_binary_others_not():
    from ag2.events.voice import SynthesizedAudioEvent

    assert is_binary_event(SynthesizedAudioEvent(b"\x00\x01"))
    assert not is_binary_event(TaskCreated("task-1"))
