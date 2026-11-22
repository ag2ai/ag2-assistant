"""Stored log change detection retains bounded bookkeeping and reloads external edits."""

import asyncio
import gc
import tracemalloc

import pytest
from ag2.events import ModelMessage, ModelResponse
from ag2.knowledge.constants import LOG_PREFIX

from tests.support.apps import real_gateway
from tests.support.fakes import TransientChatEventLog, fake_agent_factory


def gateway(config, **kwargs):
    return real_gateway(config, memory=False, agent_factory=fake_agent_factory(), **kwargs)


def flaky_gateway(config):
    """A gateway over a TransientChatEventLog, and a getter for that log."""
    logs: list[TransientChatEventLog] = []

    def chat_log(store):
        logs.append(TransientChatEventLog(store))
        return logs[-1]

    return gateway(config, chat_log_factory=chat_log), lambda: logs[-1]


@pytest.mark.parametrize("restore", [False, True], ids=["persist", "hydrate"])
async def test_loaded_logs_do_not_retain_a_second_copy_of_message_content(config, restore):
    gw = gateway(config)
    await gw.start()
    size = 2 * 1024 * 1024
    if restore:
        try:
            for index in range(4):
                await gw.emit_event(
                    f"large-{index}", ModelResponse(message=ModelMessage(content="x" * size))
                )
            assert (config.data_dir / "chats.db").is_file()
        finally:
            await gw.close()
        gw = gateway(config)
        await gw.start()
    tracing = tracemalloc.is_tracing()
    if not tracing:
        tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        for index in range(4):
            if restore:
                await gw.stream_for(f"large-{index}")
            else:
                await gw.emit_event(
                    f"large-{index}", ModelResponse(message=ModelMessage(content="x" * size))
                )
        assert (config.data_dir / "chats.db").is_file()
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        assert retained < 6 * size, f"retained {retained} bytes for {4 * size} bytes of content"
        for index in range(4):
            events = await (await gw.stream_for(f"large-{index}")).history.get_events()
            assert events[-1].message.content == "x" * size
    finally:
        if not tracing:
            tracemalloc.stop()
        await gw.close()


async def test_same_length_external_log_change_and_removal_reload_the_stream(config):
    gw = gateway(config)
    await gw.start()
    try:
        await gw.emit_event("shared", ModelResponse(message=ModelMessage(content="before")))
        path = f"{LOG_PREFIX}shared.jsonl"
        raw = await gw._event_store.read(path)
        assert raw is not None
        changed = raw.replace('"before"', '"edited"')
        assert changed != raw and len(changed) == len(raw)
        await gw._event_store.write(path, changed)
        events = await (await gw.stream_for("shared")).history.get_events()
        assert events[-1].message.content == "edited"
        await gw._event_store.delete(path)
        assert await (await gw.stream_for("shared")).history.get_events() == []
        await gw.emit_event("shared", ModelResponse(message=ModelMessage(content="reborn")))
        events = await (await gw.stream_for("shared")).history.get_events()
        assert [event.message.content for event in events] == ["reborn"]
    finally:
        await gw.close()


async def test_listing_saved_chats_does_not_retain_their_full_event_streams(config):
    size = 2 * 1024 * 1024
    writer = gateway(config)
    await writer.start()
    try:
        for index in range(8):
            await writer.send_message(f"hello-{index}", chat_id=f"saved-{index}")
            await writer.emit_event(
                f"saved-{index}", ModelResponse(message=ModelMessage(content="x" * size))
            )
    finally:
        await writer.close()
    reader = gateway(config)
    await reader.start()
    tracing = tracemalloc.is_tracing()
    if not tracing:
        tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        listed = await reader.list_chats()
        assert {row["chat_id"] for row in listed} == {f"saved-{index}" for index in range(8)}
        assert all(row["status"] == "completed" and row["unread"] for row in listed)
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        assert retained < 2 * size, f"listing retained {retained} bytes for {8 * size} content"
        assert reader.status()["chats"] == 0
        for index in range(8):
            assert (await reader.transcript(f"saved-{index}"))[0]["text"] == f"hello-{index}"
        stream = await reader.stream_for("saved-0")
        events = await stream.history.get_events()
        assert events[-1].message.content == "x" * size
        assert reader.status()["chats"] == 1
        reply = await reader.send_message("again", chat_id="saved-0")
        assert reply.endswith(": again")
        transcript = await reader.transcript("saved-0")
        assert [row["text"] for row in transcript] == [
            "hello-0",
            "echo[1]: hello-0",
            "again",
            reply,
        ]
        assert any(
            isinstance(event, ModelResponse) and event.message.content == "x" * size
            for event in await stream.history.get_events()
        )
    finally:
        if not tracing:
            tracemalloc.stop()
        await reader.close()


async def test_unopened_chat_status_tracks_external_receipts_and_retains_history(config):
    writer, reader = gateway(config), gateway(config)
    await writer.start()
    await reader.start()
    try:
        await writer.send_message("first", chat_id="shared")
        first = await reader.chat_status("shared")
        assert first["unread"] and first["status"] == "completed"
        assert reader.status()["chats"] == 0
        await writer.mark_chat_seen("shared", first["status_version"])
        assert not (await reader.chat_status("shared"))["unread"]
        assert reader.status()["chats"] == 0
        await writer.send_message("second", chat_id="shared")
        second = await reader.chat_status("shared")
        assert second["unread"] and second["status_version"] != first["status_version"]
        assert reader.status()["chats"] == 0
        seen = await reader.mark_chat_seen("shared", second["status_version"])
        assert seen is not None and not seen["unread"]
        assert reader.status()["chats"] == 1
        assert not (await writer.chat_status("shared"))["unread"]
        assert [
            row["text"] for row in await reader.transcript("shared") if row["role"] == "user"
        ] == ["first", "second"]
        assert await reader.replay_events("shared")
    finally:
        await reader.close()
        await writer.close()


async def test_unopened_status_detects_same_length_external_receipt_edits_and_removal(config):
    writer, reader = gateway(config), gateway(config)
    await writer.start()
    await reader.start()
    try:
        await writer.send_message("first", chat_id="shared")
        first = await writer.chat_status("shared")
        await writer.mark_chat_seen("shared", first["status_version"])
        assert not (await reader.chat_status("shared"))["unread"]
        path = f"{LOG_PREFIX}shared.jsonl"
        raw = await writer._event_store.read(path)
        assert raw is not None
        lines = raw.splitlines(keepends=True)
        edited = "".join(
            line.replace(first["status_version"], "0" * len(first["status_version"]))
            if "ChatSeen" in line
            else line
            for line in lines
        )
        assert edited != raw and len(edited) == len(raw)
        await writer._event_store.write(path, edited)
        assert (await reader.chat_status("shared"))["unread"]
        assert reader.status()["chats"] == 0
        await writer._event_store.delete(path)
        missing = await reader.chat_status("shared")
        assert missing["status"] is None and not missing["unread"]
        assert missing["status_version"] == ""
        await writer._event_store.write(path, raw)
        assert not (await reader.chat_status("shared"))["unread"]
        assert reader.status()["chats"] == 0
    finally:
        await reader.close()
        await writer.close()


async def test_concurrent_status_and_stream_reads_preserve_live_subscriptions(config):
    writer = gateway(config)
    await writer.start()
    try:
        await writer.send_message("first", chat_id="shared")
    finally:
        await writer.close()
    reader = gateway(config)
    await reader.start()
    received = []
    try:
        status, stream = await asyncio.gather(
            reader.chat_status("shared"), reader.stream_for("shared")
        )
        assert status["unread"] and status["status"] == "completed"
        assert await stream.history.get_events()

        async def observe(event):
            if isinstance(event, ModelResponse):
                received.append(event.message.content)

        stream.subscribe(observe)
        await asyncio.gather(reader.list_chats(), reader.chat_status("shared"))
        await reader.emit_event("shared", ModelResponse(message=ModelMessage(content="live")))
        assert received == ["live"]
        assert await reader.stream_for("shared") is stream
    finally:
        await reader.close()


@pytest.mark.parametrize("opened", [False, True], ids=["unopened", "opened"])
@pytest.mark.parametrize("primed", [False, True], ids=["first-load", "external-update"])
@pytest.mark.parametrize("stage", ["load", "replay"])
async def test_transient_history_failure_retries_without_a_log_change(
    config, opened, primed, stage
):
    writer = gateway(config)
    reader, log = flaky_gateway(config)
    await writer.start()
    await reader.start()
    try:
        if primed:
            await writer.send_message("first", chat_id="shared")
        previous = await reader.chat_status("shared")
        if opened:
            await reader.stream_for("shared")
        await writer.send_message("after", chat_id="shared")
        expected = await writer.chat_status("shared")
        path = f"{LOG_PREFIX}shared.jsonl"
        stored = await writer._event_store.read(path)
        log().fail_once(stage)

        assert await reader.chat_status("shared") == previous
        recovered = await reader.chat_status("shared")
        assert recovered == expected
        assert recovered["status_version"] != previous["status_version"]
        assert reader.status()["chats"] == int(opened)
        assert await writer._event_store.read(path) == stored
        assert await reader.chat_status("shared") == recovered
        assert await (await reader.stream_for("shared")).history.get_events()
        messages = [
            row["text"] for row in await reader.transcript("shared") if row["role"] == "user"
        ]
        assert messages == (["first", "after"] if primed else ["after"])
    finally:
        await reader.close()
        await writer.close()


@pytest.mark.parametrize("stage", ["load", "replay"])
async def test_failed_first_open_retries_and_keeps_the_sidebar_status(config, stage):
    writer = gateway(config)
    reader, log = flaky_gateway(config)
    await writer.start()
    await reader.start()
    try:
        await writer.send_message("first", chat_id="shared")
        listed = await reader.chat_status("shared")
        log().fail_once(stage)

        await reader.stream_for("shared")
        assert await reader.chat_status("shared") == listed
        events = await (await reader.stream_for("shared")).history.get_events()
        assert events
        assert await reader.chat_status("shared") == listed
        messages = [
            row["text"] for row in await reader.transcript("shared") if row["role"] == "user"
        ]
        assert messages == ["first"]
    finally:
        await reader.close()
        await writer.close()
