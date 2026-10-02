"""AG2 Assistant custom events round-trip through the AG2 serialize/persist contract.

The whole GUI-redesign rests on this: app events serialize to `{type, data}`,
persist via EventLogWriter, and reload by dynamic class import — exactly like
native AG2 events. If these break, history/replay break.
"""

import pytest
from ag2.events.voice import SynthesizedAudioEvent
from ag2.knowledge.log import import_event_class

from assistant.a2ui import CARD_VOCABULARY, CardCatalog
from assistant.coding.diff import FileDiff
from assistant.coding.surface import build_surface, card_fields
from assistant.events import (
    A2UISurface,
    DeliverableProduced,
    InquiryAnswered,
    InquiryRaised,
    TaskCreated,
    TaskScheduled,
)
from assistant.gateway.wire import as_drawn, is_binary_event, to_wire

_SAMPLES = [
    TaskCreated("task-1", title="Weather report", kind="scheduled"),
    TaskScheduled("task-1", scheduled_for="2026-06-18T08:00:00+10:00", recurrence="0 8 * * *"),
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
    A2UISurface(
        "surface-1",
        title="Weather",
        intent="weather",
        component={"component": "WeatherPanel"},
        data={"location": "Sydney"},
    ),
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

    assert is_binary_event(SynthesizedAudioEvent(b"\x00\x01"))
    assert not is_binary_event(TaskCreated("task-1"))


# --- What is stored, and what a client is sent (ADR 0036) -------------------


def _coding_instance() -> A2UISurface:
    return build_surface(
        "cs1",
        card_fields(
            agent_label="Claude Code",
            directory="/repo",
            task="add hello",
            status="done",
            files=[FileDiff("hello.py", "added", "@@ -0,0 +1 @@\n+hi\n", 1, 0)],
        ),
    )


def test_a_card_instance_is_stored_as_the_fields_its_author_filled_in():
    stored = to_wire(_coding_instance())["data"]

    assert stored["component"]["component"] == "CodingSession"
    assert stored["data"]["agent"] == "Claude Code"


def test_a_card_instance_reaches_a_client_drawn_as_its_layout_declares(config):
    # Every projection asks `as_drawn`, so a Card the browser was never taught
    # arrives as primitives whether it came over the chat socket or the voice one.
    sent = to_wire(as_drawn(_coding_instance(), CardCatalog(config)))["data"]

    assert sent["component"]["component"] == "Card"
    assert {node["component"] for node in sent["component"]["_components"]} <= CARD_VOCABULARY


def test_anything_that_is_not_a_surface_is_passed_through_untouched(config):
    event = TaskCreated("task-1", title="Weather report", kind="scheduled")

    assert as_drawn(event, CardCatalog(config)) is event
