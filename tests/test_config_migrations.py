"""Existing model settings survive the AG2 1.3 upgrade without unsupported sampling."""

import pytest

from assistant.agent import model_config
from assistant.config import Config, resolve_config
from assistant.config_migrations import migrate_anthropic_sampling, sampling_marker
from assistant.gateway.profile_manager import resolve_active_profile
from assistant.llm_configs import LlmConfigStore
from assistant.model_params import validate_anthropic_options
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

    migrate_anthropic_sampling(paths.config_yaml)
    migrate_anthropic_sampling(profile)
    cfg = resolve_config({}, paths)
    entry = LlmConfigStore(paths).get_config("a")
    assert entry["params"] == {"max_tokens": 2048}
    assert entry["options"] == {"extra_body": {"metadata": {"user_id": "u"}}}
    assert read_yaml(paths.config_yaml)["llm_configs"]["configs"][1] == other
    assert cfg.agent.name == "Ada"
    assert cfg.llm.provider_options["anthropic"]["max_tokens"] == 2048
    assert read_yaml(profile)["llm"]["provider_options"]["anthropic"] == {"max_tokens": 1024}

    global_stamp, profile_stamp = paths.config_yaml.stat().st_mtime_ns, profile.stat().st_mtime_ns
    migrate_anthropic_sampling(paths.config_yaml)
    migrate_anthropic_sampling(profile)
    assert (paths.config_yaml.stat().st_mtime_ns, profile.stat().st_mtime_ns) == (
        global_stamp,
        profile_stamp,
    )
    assert sampling_marker(paths.config_yaml).exists()
    assert sampling_marker(profile).exists()


@pytest.mark.parametrize("nested", [False, True])
def test_migration_adopts_persisted_option_pairs_without_changing_other_models(paths, nested):
    options = (
        [["extra_body", {"top_p": 0.5, "metadata": {"user_id": "u"}}]]
        if nested
        else [["temperature", 0.4]]
    )
    options.append(["max_tokens", 1024])
    entry = {
        "id": "a",
        "name": "Claude",
        "type": "anthropic",
        "model": "claude",
        "options": options,
    }
    other = {**entry, "id": "g", "type": "gemini"}
    write_yaml(paths.config_yaml, {"llm_configs": {"active": "a", "configs": [entry, other]}})
    migrate_anthropic_sampling(paths.config_yaml)
    store = LlmConfigStore(paths)
    assert store.get_config("g") == other
    assert store.get_config("a")["options"] == {
        "max_tokens": 1024,
        **({"extra_body": {"metadata": {"user_id": "u"}}} if nested else {}),
    }
    resolved = resolve_config({}, paths).llm.provider_options["anthropic"]
    validate_anthropic_options(resolved)
    assert resolved["max_tokens"] == 1024


def test_migration_preserves_yaml_aliases_owned_by_other_providers(paths):
    paths.root.mkdir(parents=True)
    paths.config_yaml.write_text("""llm:
  provider_options:
    gemini: &options
      temperature: 0.4
      extra_body: &body
        top_p: 0.5
        metadata: {user_id: u}
    anthropic: *options
llm_configs:
  configs:
    - id: g
      type: gemini
      params: &params {temperature: 0.3, max_tokens: 500}
      options: *options
    - id: a
      type: anthropic
      params: *params
      options: *options
other: *body
""")
    before = read_yaml(paths.config_yaml)
    migrate_anthropic_sampling(paths.config_yaml)
    after = read_yaml(paths.config_yaml)
    assert after["llm"]["provider_options"]["gemini"] == before["llm"]["provider_options"]["gemini"]
    assert after["llm_configs"]["configs"][0] == before["llm_configs"]["configs"][0]
    assert after["other"] == before["other"]
    assert after["llm"]["provider_options"]["anthropic"] == {
        "extra_body": {"metadata": {"user_id": "u"}}
    }
    assert after["llm_configs"]["configs"][1]["params"] == {"max_tokens": 500}


@pytest.mark.parametrize("content", ["llm: [broken", "- not a mapping"])
def test_bad_yaml_is_retryable_after_repair(paths, content):
    paths.root.mkdir(parents=True)
    paths.config_yaml.write_text(content)
    migrate_anthropic_sampling(paths.config_yaml)
    assert not sampling_marker(paths.config_yaml).exists()
    write_yaml(
        paths.config_yaml,
        {"llm": {"provider": "anthropic", "provider_options": {"anthropic": {"temperature": 0.4}}}},
    )
    migrate_anthropic_sampling(paths.config_yaml)
    cfg = resolve_config({}, paths)
    assert cfg.llm.provider == "anthropic"
    validate_anthropic_options(cfg.llm.provider_options["anthropic"])
    assert read_yaml(paths.config_yaml)["llm"]["provider_options"]["anthropic"] == {}
    assert sampling_marker(paths.config_yaml).exists()


def test_unreadable_or_missing_config_is_not_marked_complete(paths):
    migrate_anthropic_sampling(paths.config_yaml)
    assert not sampling_marker(paths.config_yaml).exists()
    paths.config_yaml.mkdir(parents=True)
    migrate_anthropic_sampling(paths.config_yaml)
    assert not sampling_marker(paths.config_yaml).exists()
    paths.config_yaml.rmdir()
    write_yaml(paths.config_yaml, {"llm": {"provider_options": {"anthropic": {"top_k": 5}}}})
    migrate_anthropic_sampling(paths.config_yaml)
    assert read_yaml(paths.config_yaml)["llm"]["provider_options"]["anthropic"] == {}


@pytest.mark.parametrize("archived", [False, True])
async def test_failed_profile_migration_boots_every_profile_and_retries(paths, archived):
    registry = ProfileRegistry(paths)
    healthy = registry.create_profile("Healthy", "#109e91")
    broken = registry.create_profile("Broken", "#ffa000")
    if archived:
        registry.archive_profile(broken.id)
    path = paths.profile_dir(broken.id) / "config.yaml"
    write_yaml(path, {"llm": {"provider_options": {"anthropic": {"temperature": 0.4}}}})
    path.with_name("config.yaml.tmp").mkdir()
    manager = make_manager(paths)
    await manager.start()
    try:
        assert manager.gateway_for_profile(healthy.id) is not None
        assert (manager.gateway_for_profile(broken.id) is None) is archived
        assert broken.id not in manager.boot_errors
        assert await manager.get(healthy.id).gateway.send_message("hello", chat_id="healthy")
        _, config, _ = resolve_active_profile(healthy.id, paths=paths, env={})
        assert config.data_dir == paths.profile_dir(healthy.id)
    finally:
        await manager.close()
    assert not sampling_marker(path).exists()
    path.with_name("config.yaml.tmp").rmdir()
    if archived:
        registry.restore_profile(broken.id)
    manager = make_manager(paths)
    await manager.start()
    try:
        assert manager.gateway_for_profile(broken.id) is not None
        assert read_yaml(path)["llm"]["provider_options"]["anthropic"] == {}
    finally:
        await manager.close()


def test_interrupted_sampling_migration_resumes_before_recording_completion(paths):
    data = {"llm": {"provider_options": {"anthropic": {"temperature": 0.4, "max_tokens": 500}}}}
    write_yaml(paths.config_yaml, data)
    profile = paths.profile_dir("work") / "config.yaml"
    write_yaml(profile, data)
    blocked_temp = profile.with_name("config.yaml.tmp")
    blocked_temp.mkdir()
    migrate_anthropic_sampling(paths.config_yaml)
    migrate_anthropic_sampling(profile)
    assert not sampling_marker(profile).exists()
    assert read_yaml(profile) == data
    assert read_yaml(paths.config_yaml)["llm"]["provider_options"]["anthropic"] == {
        "max_tokens": 500
    }
    blocked_temp.rmdir()
    migrate_anthropic_sampling(paths.config_yaml)
    migrate_anthropic_sampling(profile)
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
