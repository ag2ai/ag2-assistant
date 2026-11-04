"""Existing model settings survive the AG2 1.3 upgrade without unsupported sampling."""

import pytest

from assistant.agent import model_config
from assistant.config import Config, resolve_config
from assistant.config_migrations import migrate_anthropic_sampling
from assistant.llm_configs import LlmConfigStore
from assistant.profiles import ProfileRegistry
from assistant.yamlio import read_yaml, write_yaml
from tests.support.apps import make_manager
from tests.support.fakes import ScriptedModels


def test_anthropic_sampling_is_removed_once_from_all_persisted_configs(paths):
    anthropic = {
        "id": "a",
        "name": "Claude",
        "type": "anthropic",
        "model": "claude",
        "params": {"temperature": 0.4, "max_tokens": 2048},
        "options": {"top_p": 0.5, "extra_body": {"top_k": 10, "metadata": {"user_id": "u"}}},
    }
    other = {"id": "g", "type": "gemini", "params": {"temperature": 0.4}}
    write_yaml(
        paths.config_yaml,
        {"llm_configs": {"active": "a", "configs": [anthropic, other]}, "agent": {"name": "Ada"}},
    )
    profile = paths.profile_dir("work") / "config.yaml"
    write_yaml(
        profile,
        {"llm": {"provider_options": {"anthropic": {"temperature": 1, "max_tokens": 1024}}}},
    )

    migrate_anthropic_sampling(paths)
    cfg = resolve_config({}, paths)
    entry = LlmConfigStore(paths).get_config("a")
    assert entry["params"] == {"max_tokens": 2048}
    assert entry["options"] == {"extra_body": {"metadata": {"user_id": "u"}}}
    assert read_yaml(paths.config_yaml)["llm_configs"]["configs"][1] == other
    assert cfg.agent.name == "Ada"
    assert cfg.llm.provider_options["anthropic"]["max_tokens"] == 2048
    assert read_yaml(profile)["llm"]["provider_options"]["anthropic"] == {"max_tokens": 1024}

    global_stamp, profile_stamp = paths.config_yaml.stat().st_mtime_ns, profile.stat().st_mtime_ns
    migrate_anthropic_sampling(paths)
    assert (paths.config_yaml.stat().st_mtime_ns, profile.stat().st_mtime_ns) == (
        global_stamp,
        profile_stamp,
    )
    assert (paths.root / ".migrations" / "ag2-1.3-anthropic-sampling").exists()


def test_interrupted_sampling_migration_resumes_before_recording_completion(paths):
    data = {"llm": {"provider_options": {"anthropic": {"temperature": 0.4, "max_tokens": 500}}}}
    write_yaml(paths.config_yaml, data)
    profile = paths.profile_dir("work") / "config.yaml"
    write_yaml(profile, data)
    blocked_temp = profile.with_name("config.yaml.tmp")
    blocked_temp.mkdir()
    with pytest.raises(IsADirectoryError):
        migrate_anthropic_sampling(paths)
    assert not (paths.root / ".migrations" / "ag2-1.3-anthropic-sampling").exists()
    assert read_yaml(paths.config_yaml)["llm"]["provider_options"]["anthropic"] == {
        "max_tokens": 500
    }
    blocked_temp.rmdir()
    migrate_anthropic_sampling(paths)
    assert read_yaml(profile)["llm"]["provider_options"]["anthropic"] == {"max_tokens": 500}


async def test_gateway_start_adopts_saved_options_before_the_first_turn(paths):
    write_yaml(
        paths.config_yaml,
        {
            "llm": {
                "provider": "anthropic",
                "provider_options": {"anthropic": {"temperature": 0.4, "max_tokens": 500}},
            }
        },
    )
    profile = ProfileRegistry(paths).create_profile("Work", "#109e91")
    models = ScriptedModels()
    manager = make_manager(paths, model_factory=models)
    await manager.start()
    try:
        reply = await manager.get(profile.id).gateway.send_message("hello", chat_id="first")
        assert reply == "done"
        assert models.requests[-1][0].llm.provider_options["anthropic"] == {"max_tokens": 500}
    finally:
        await manager.close()


@pytest.mark.parametrize("key", ["temperature", "top_p", "top_k"])
@pytest.mark.parametrize("nested", [False, True])
def test_new_anthropic_sampling_options_are_refused_at_save_and_model_boundaries(
    paths, key, nested
):
    options = {"extra_body": {key: 0.5}} if nested else {key: 0.5}
    with pytest.raises(ValueError, match=key):
        LlmConfigStore(paths).save_config(
            {"name": "A", "type": "anthropic", "model": "claude", "options": options}
        )
    cfg = Config.for_paths(paths)
    cfg.llm.provider = "anthropic"
    cfg.llm.provider_options = {"anthropic": options}
    with pytest.raises(ValueError, match=key):
        model_config(cfg)
