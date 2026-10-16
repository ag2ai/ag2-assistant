"""Owned SQLite operations finish before cancellation can release their connection."""

import asyncio
import gc
import sqlite3
import weakref

import pytest

from assistant.resources import OwnedSqliteStore
from tests.support.fakes import SignallingSqliteStore


@pytest.mark.parametrize("cancel_writer", [False, True])
async def test_store_shutdown_waits_for_a_blocked_sqlite_write(tmp_path, cancel_writer):
    path = tmp_path / "store.db"
    backend = SignallingSqliteStore(path)
    store = OwnedSqliteStore(backend)
    await store.write("/entry", "original")
    with sqlite3.connect(path) as other:
        other.execute("BEGIN IMMEDIATE")
        writer = asyncio.create_task(store.write("/entry", "blocked"))
        await asyncio.wait_for(backend.entered.wait(), timeout=2)
        if cancel_writer:
            writer.cancel()
            writer.cancel()
        closing = asyncio.create_task(store.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        assert not writer.done()
        other.commit()
        if cancel_writer:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(writer, timeout=2)
        else:
            await asyncio.wait_for(writer, timeout=2)
        await asyncio.wait_for(closing, timeout=2)
    with sqlite3.connect(path) as fresh:
        assert fresh.execute("SELECT content FROM entries WHERE path = '/entry'").fetchone() == (
            b"blocked",
        )
    with pytest.raises(RuntimeError, match="closed"):
        await store.read("/entry")
    await store.aclose()


async def test_store_change_subscription_stops_at_owned_shutdown(tmp_path):
    store = OwnedSqliteStore(SignallingSqliteStore(tmp_path / "store.db"))
    changed = asyncio.Event()

    async def callback(path):
        if await store.read(path) == "updated":
            changed.set()

    await store.on_change("/", callback)
    await store.write("/entry", "updated")
    await asyncio.wait_for(changed.wait(), timeout=2)
    await store.aclose()


async def test_cancelled_write_remains_cancelled_when_sqlite_lock_times_out(tmp_path):
    path = tmp_path / "store.db"
    backend = SignallingSqliteStore(path)
    store = OwnedSqliteStore(backend)
    await store.write("/entry", "original")
    try:
        with sqlite3.connect(path) as other:
            other.execute("BEGIN IMMEDIATE")
            writer = asyncio.create_task(store.write("/entry", "blocked"))
            await asyncio.wait_for(backend.entered.wait(), timeout=2)
            writer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(writer, timeout=8)
        assert await store.read("/entry") == "original"
    finally:
        await store.aclose()


async def test_closed_subscription_releases_its_callback_without_closing_the_store(tmp_path):
    store = OwnedSqliteStore(SignallingSqliteStore(tmp_path / "store.db"))

    async def callback(path):
        await store.read(path)

    reference = weakref.ref(callback)
    subscription = await store.on_change("/", callback)
    await subscription.close()
    del callback, subscription
    gc.collect()
    assert reference() is None

    changed = asyncio.Event()

    async def next_callback(path):
        if await store.read(path) == "updated":
            changed.set()

    await store.on_change("/", next_callback)
    await store.write("/entry", "updated")
    await asyncio.wait_for(changed.wait(), timeout=2)
    await store.aclose()


async def test_cancelled_subscription_acquisition_releases_its_callback(tmp_path):
    path = tmp_path / "store.db"
    backend = SignallingSqliteStore(path)
    store = OwnedSqliteStore(backend)
    await store.write("/entry", "original")

    async def callback(path):
        await store.read(path)

    reference = weakref.ref(callback)
    try:
        with sqlite3.connect(path) as other:
            other.execute("BEGIN EXCLUSIVE")
            subscribing = asyncio.create_task(store.on_change("/", callback))
            await asyncio.wait_for(backend.reading.wait(), timeout=2)
            subscribing.cancel()
            other.commit()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(subscribing, timeout=2)
        del callback, subscribing
        assert await store.read("/entry") == "original"
        gc.collect()
        assert reference() is None
    finally:
        await store.aclose()
