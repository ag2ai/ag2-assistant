"""Chat metadata updates: rename (title) + star/unstar, persisted in the transcript doc."""

import pytest

from assistant.config import Config
from assistant.gateway.core import Gateway
from tests.support.apps import api
from tests.support.fakes import fake_agent_factory, fake_title_factory


def _gateway(paths, tmp_path, **kwargs) -> Gateway:
    """A gateway on an isolated layout whose agent never reaches an LLM."""
    return Gateway(
        config=Config.for_paths(paths, data_dir=tmp_path),
        memory=False,
        agent_factory=fake_agent_factory(),
        **kwargs,
    )


@pytest.fixture
async def gw(paths, tmp_path):
    gw = _gateway(paths, tmp_path)
    await gw.start()
    yield gw
    await gw.close()


async def test_update_chat_title_persists_across_instances(paths, gw, tmp_path):

    await gw.send_message("hello", chat_id="c1")
    assert await gw.update_chat("c1", title="Renamed by user") is True
    assert (
        next(c for c in await gw.list_chats() if c["chat_id"] == "c1")["title"] == "Renamed by user"
    )

    await gw.close()
    gw2 = _gateway(paths, tmp_path)
    await gw2.start()
    assert (
        next(c for c in await gw2.list_chats() if c["chat_id"] == "c1")["title"]
        == "Renamed by user"
    )
    await gw2.close()


async def test_update_chat_starred_roundtrip(gw):
    await gw.send_message("hello", chat_id="c1")
    listed = next(c for c in await gw.list_chats() if c["chat_id"] == "c1")
    assert listed["starred"] is False  # default: unstarred

    assert await gw.update_chat("c1", starred=True) is True
    assert next(c for c in await gw.list_chats() if c["chat_id"] == "c1")["starred"] is True
    assert await gw.update_chat("c1", starred=False) is True
    assert next(c for c in await gw.list_chats() if c["chat_id"] == "c1")["starred"] is False


async def test_update_chat_partial_leaves_other_field(gw):
    await gw.send_message("hello", chat_id="c1")
    await gw.update_chat("c1", title="Kept title")
    await gw.update_chat("c1", starred=True)  # no title in this patch
    c = next(c for c in await gw.list_chats() if c["chat_id"] == "c1")
    assert c["title"] == "Kept title"
    assert c["starred"] is True

    # An oversize user title is capped at 200 chars in the stored doc.
    await gw.update_chat("c1", title="x" * 500)
    c = next(c for c in await gw.list_chats() if c["chat_id"] == "c1")
    assert len(c["title"]) == 200


async def test_update_unknown_chat_returns_false(gw):
    assert await gw.update_chat("missing", title="x") is False


async def test_user_rename_wins_over_auto_titler(paths, tmp_path):
    """The auto-titler skips already-titled chats, so a user rename sticks."""
    # A titler that really does produce a title — the skip-if-titled guard is what
    # must keep the user's rename intact.
    gw = _gateway(paths, tmp_path, title_factory=fake_title_factory("LLM title"))
    await gw.start()
    try:
        await gw.send_message("hello", chat_id="c1")
        await gw.update_chat("c1", title="User title")
        await gw._title_chat("c1", "hello", "echo[1]: hello")  # would set "LLM title"
        chat = next(c for c in await gw.list_chats() if c["chat_id"] == "c1")
        assert chat["title"] == "User title"
    finally:
        await gw.close()


async def test_the_auto_titler_names_an_untitled_chat(paths, tmp_path):
    """The other side of the guard: with no user rename, the titler's title lands."""
    gw = _gateway(paths, tmp_path, title_factory=fake_title_factory("LLM title"))
    await gw.start()
    try:
        await gw.send_message("hello", chat_id="c1")
        await gw._title_chat("c1", "hello", "echo[1]: hello")
        chat = next(c for c in await gw.list_chats() if c["chat_id"] == "c1")
        assert chat["title"] == "LLM title"
    finally:
        await gw.close()


# --- REST facade ---


def test_patch_chat_route(profile_app):
    """PATCH /chats/{chat_id}: 200 {ok}, 404 unknown, 400 empty patch."""
    client, pid = profile_app  # conftest fixture: started app, persist=True, agent faked
    client.post(api(pid, "/message"), json={"text": "hi", "chat_id": "c1"})

    r = client.patch(api(pid, "/chats/c1"), json={"title": "Named", "starred": True})
    assert r.status_code == 200 and r.json() == {"ok": True}
    chats = client.get(api(pid, "/chats")).json()["chats"]
    c1 = next(c for c in chats if c["chat_id"] == "c1")
    assert c1["title"] == "Named" and c1["starred"] is True

    # unknown chat → 404; empty patch → 400
    assert client.patch(api(pid, "/chats/nope"), json={"title": "x"}).status_code == 404
    r = client.patch(api(pid, "/chats/c1"), json={})
    assert r.status_code == 400 and r.json()["error"] == "empty patch"
