"""A profile's memory.shared setting governs the remember tool and the shared prompt."""

from contextlib import AsyncExitStack

import pytest
from ag2 import Context
from ag2.testing import TestConfig
from httpx import ASGITransport, AsyncClient

from assistant.agent import build_memory_tool, universal_memory_guidance
from assistant.config import Config
from assistant.gateway.app import create_app
from assistant.memory import read_profile_sync, write_universal
from assistant.profiles import ProfileRegistry
from assistant.settings import profile_settings
from assistant.yamlio import read_yaml
from tests.support.apps import api, make_manager
from tests.support.fakes import ScriptedModels

WHO = "Who the user is (shared across all profiles)"


def _remember_schema(tools):
    (schema,) = [s for s in tools if s.function.name == "remember"]
    return schema.function.parameters


async def _call(tool, **kwargs):
    async with AsyncExitStack() as stack:
        return await tool.model.asolve(stack=stack, cache_dependencies={}, **kwargs)


async def _schemas(remember):
    return [s for s in await remember.schemas(Context(stream=None))]


async def test_read_write_offers_both_scopes_and_writes_the_shared_doc(tmp_path):
    remember = build_memory_tool(tmp_path / "profile.db", tmp_path / "user.db")
    params = _remember_schema(await _schemas(remember))
    assert params["properties"]["scope"]["enum"] == ["profile", "universal"]

    await _call(remember, note="Name is Sam", category="how", scope="universal")
    assert "Name is Sam" in read_profile_sync(tmp_path / "user.db")
    assert not (tmp_path / "profile.db").exists()


@pytest.mark.parametrize("mode", ["read", "off"])
async def test_read_and_off_offer_no_scope_and_write_only_this_profile(tmp_path, mode):
    remember = build_memory_tool(tmp_path / "profile.db", tmp_path / "user.db", shared=mode)
    params = _remember_schema(await _schemas(remember))
    assert set(params["properties"]) == {"note", "category"}
    assert "universal" not in str(params)

    reply = await _call(remember, note="Likes short answers", category="how")
    assert reply == "Saved to this profile's memory (under 'how')."
    assert "Likes short answers" in read_profile_sync(tmp_path / "profile.db")
    assert not (tmp_path / "user.db").exists()


@pytest.mark.parametrize("mode, shown", [("read_write", True), ("read", True), ("off", False)])
async def test_the_shared_doc_is_injected_unless_off(paths, tmp_path, mode, shown):
    config = Config.for_paths(paths)
    config.root_dir = tmp_path
    await write_universal("- Name: Sam", tmp_path / "user.db")
    config.memory.shared = mode
    section = universal_memory_guidance(config)
    assert (WHO in section and "Name: Sam" in section) == shown
    assert section or not shown


def test_the_default_is_read_write(paths):
    assert Config.for_paths(paths).memory.shared == "read_write"


async def test_the_setting_applies_to_the_next_turn_of_one_profile_only(paths):
    registry = ProfileRegistry(paths)
    guest = registry.create_profile("Shared", "#109e91")
    mine = registry.create_profile("Mine", "#109e91")
    for meta in (guest, mine):
        paths.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    await write_universal("- Name: Sam", paths.root / "user.db")
    models = ScriptedModels()
    manager = make_manager(paths, persist=True, memory=True, model_factory=models)
    app = create_app(manager)

    async def turn(pid):
        await manager.get(pid).require_gateway().send_message("hi", chat_id="c")
        _, prompt, tools, _, _ = models.requests[-1]
        return " ".join(prompt), _remember_schema(tools)["properties"]

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            assert (await http.get(api(guest.id, "/settings"))).json()["shared_memory"] == (
                "read_write"
            )
            prompt, props = await turn(guest.id)
            assert WHO in prompt and "scope" in props

            for mode, shown in (("read", True), ("off", False), ("read_write", True)):
                saved = await http.post(
                    api(guest.id, "/settings/shared-memory"), json={"shared_memory": mode}
                )
                assert saved.status_code == 200, saved.text
                assert saved.json() == {"ok": True, "shared_memory": mode}
                assert (await http.get(api(guest.id, "/settings"))).json()["shared_memory"] == mode
                prompt, props = await turn(guest.id)
                assert (WHO in prompt) == shown
                assert ("scope" in props) == (mode == "read_write")

            await http.post(api(guest.id, "/settings/shared-memory"), json={"shared_memory": "off"})
            prompt, props = await turn(mine.id)
            assert WHO in prompt and "scope" in props
            mine_settings = await http.get(api(mine.id, "/settings"))
            assert mine_settings.json()["shared_memory"] == "read_write"

    restarted = make_manager(paths, persist=True)
    await restarted.start()
    try:
        assert restarted.get(guest.id).require_config().memory.shared == "off"
        assert restarted.get(mine.id).require_config().memory.shared == "read_write"
    finally:
        await restarted.close()


async def test_an_unknown_mode_is_rejected_without_changing_the_setting(paths):
    registry = ProfileRegistry(paths)
    meta = registry.create_profile("Shared", "#109e91")
    paths.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = make_manager(paths, persist=True, model_factory=lambda cfg, m=None: TestConfig("ok"))
    app = create_app(manager)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            for value in ["write", "", None, True, "READ"]:
                r = await http.post(
                    api(meta.id, "/settings/shared-memory"), json={"shared_memory": value}
                )
                assert r.status_code == 422, r.text
            assert (await http.get(api(meta.id, "/settings"))).json()["shared_memory"] == (
                "read_write"
            )


def test_the_settings_store_keeps_only_known_modes(tmp_path):
    settings = profile_settings(tmp_path)
    assert settings.set_shared_memory("read") == "read"
    with pytest.raises(ValueError):
        settings.set_shared_memory("write")
    assert read_yaml(tmp_path / "config.yaml")["memory"] == {"shared": "read"}
