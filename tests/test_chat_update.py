"""Chat metadata updates: rename (title) + star/unstar, persisted in the transcript doc."""

import pytest

from tests.conftest import FakeAgent


@pytest.fixture
async def gw(tmp_path, monkeypatch):
    import assistant.gateway.core as core_mod
    from assistant.config import Config
    from assistant.gateway.core import Gateway

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: FakeAgent())
    gw = Gateway(config=Config(data_dir=tmp_path), memory=False)
    await gw.start()
    yield gw
    await gw.close()


async def test_update_chat_title_persists_across_instances(gw, tmp_path, monkeypatch):
    import assistant.gateway.core as core_mod
    from assistant.config import Config
    from assistant.gateway.core import Gateway

    await gw.send_message("hello", chat_id="c1")
    assert await gw.update_chat("c1", title="Renamed by user") is True
    assert next(c for c in await gw.list_chats() if c["chat_id"] == "c1")["title"] == "Renamed by user"

    await gw.close()
    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: FakeAgent())
    gw2 = Gateway(config=Config(data_dir=tmp_path), memory=False)
    await gw2.start()
    assert next(c for c in await gw2.list_chats() if c["chat_id"] == "c1")["title"] == "Renamed by user"
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


async def test_update_unknown_chat_returns_false(gw):
    assert await gw.update_chat("missing", title="x") is False


async def test_user_rename_wins_over_auto_titler(gw, monkeypatch):
    """The auto-titler skips already-titled chats, so a user rename sticks."""
    import assistant.title

    async def fake_generate_title(*a, **k):
        return "LLM title"

    # _title_chat imports generate_title function-locally, so patching the source
    # module makes the titler genuinely produce a title — the skip-if-titled guard
    # is what must keep the user's rename intact.
    monkeypatch.setattr(assistant.title, "generate_title", fake_generate_title)

    await gw.send_message("hello", chat_id="c1")
    await gw.update_chat("c1", title="User title")
    await gw._title_chat("c1", "hello", "echo[1]: hello")  # would set "LLM title"
    assert next(c for c in await gw.list_chats() if c["chat_id"] == "c1")["title"] == "User title"
