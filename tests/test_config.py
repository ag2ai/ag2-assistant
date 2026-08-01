"""Config resolution: defaults ← config.yaml ← env, as a pure function over a dict.

``resolve_config(env, paths)`` is the whole story; ``load_config()`` is the only
boundary that reads the real ``os.environ`` and ``Path.home()``.
"""

import json
import os
from pathlib import Path

import pytest

from assistant.agent import model_config
from assistant.config import (
    AgentConfig,
    Config,
    LLMConfig,
    load_config,
    read_yaml,
    resolve_config,
    write_yaml,
)
from assistant.llm_configs import LlmConfigStore
from assistant.paths import Paths
from assistant.profiles import ProfileMeta


def test_default_config(paths):
    config = Config.for_paths(paths)
    assert config.llm.provider == "gemini"
    assert config.llm.model.startswith("gemini")
    assert config.llm.streaming is True
    assert config.agent.name == "ag2-assistant"
    assert config.data_dir == paths.root


def test_default_timeout_and_silence_thresholds(paths):
    llm = Config.for_paths(paths).llm
    assert llm.call_timeout_s == 180.0
    assert llm.call_retries == 2
    assert llm.silence_alert_s == 300.0
    assert llm.silence_halt_s == 900.0
    assert Config.for_paths(paths).gateway.reply_timeout_s == 600.0


def test_for_paths_requires_paths_and_wires_every_path_field(paths):
    cfg = Config.for_paths(paths)
    assert cfg.root_dir == paths.root
    assert cfg.data_dir == paths.root
    assert cfg.skills_dir == paths.skills_dir
    assert cfg.workspace_dir == paths.workspace


def test_config_rejects_construction_without_paths():
    """No path field may quietly default to $HOME — the layout must be handed in."""
    with pytest.raises(Exception) as exc:
        Config()
    assert "root_dir" in str(exc.value) or "data_dir" in str(exc.value)


def test_custom_llm_config(paths):
    llm = LLMConfig(provider="openai", model="gpt-4o", api_key_env="OPENAI_API_KEY")
    config = Config.for_paths(paths, llm=llm)
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o"


def test_custom_agent_config(paths):
    agent = AgentConfig(name="test-agent", system_prompt="You are a test agent.")
    config = Config.for_paths(paths, agent=agent)
    assert config.agent.name == "test-agent"
    assert "test agent" in config.agent.system_prompt


def test_defaults_apply_when_nothing_is_configured(paths):
    cfg = resolve_config({}, paths)
    assert cfg.llm.provider == "gemini"
    assert cfg.data_dir == paths.root
    assert cfg.workspace_dir == paths.workspace


def test_config_yaml_overrides_defaults(paths):
    write_yaml(paths.config_yaml, {"llm": {"provider": "anthropic", "model": "opus"}})
    cfg = resolve_config({}, paths)
    assert (cfg.llm.provider, cfg.llm.model) == ("anthropic", "opus")


def test_env_overrides_config_yaml(paths):
    write_yaml(paths.config_yaml, {"llm": {"provider": "anthropic", "model": "opus"}})
    cfg = resolve_config({"AG2ASSISTANT_LLM_PROVIDER": "openai"}, paths)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "opus"  # an untouched field survives the override


def test_resolve_config_does_not_read_the_process_environment(paths):
    """Resolution is pure: the real process environment must not influence it."""
    os.environ["AG2ASSISTANT_LLM_PROVIDER"] = "should-be-ignored"
    try:
        assert resolve_config({}, paths).llm.provider == "gemini"
    finally:
        del os.environ["AG2ASSISTANT_LLM_PROVIDER"]


def test_timeout_and_silence_env_overrides(paths):
    cfg = resolve_config(
        {
            "AG2ASSISTANT_LLM_TIMEOUT": "45",
            "AG2ASSISTANT_LLM_RETRIES": "5",
            "AG2ASSISTANT_SILENCE_ALERT": "120",
            "AG2ASSISTANT_SILENCE_HALT": "600",
            "AG2ASSISTANT_REPLY_TIMEOUT": "480",
        },
        paths,
    )
    assert cfg.llm.call_timeout_s == 45.0
    assert cfg.llm.call_retries == 5
    assert cfg.llm.silence_alert_s == 120.0
    assert cfg.llm.silence_halt_s == 600.0
    assert cfg.gateway.reply_timeout_s == 480.0


def test_bad_numeric_env_is_ignored_not_fatal(paths):
    cfg = resolve_config(
        {
            "AG2ASSISTANT_LLM_TIMEOUT": "not-a-number",
            "AG2ASSISTANT_LLM_RETRIES": "not-a-number",
            "AG2ASSISTANT_REPLY_TIMEOUT": "not-a-number",
        },
        paths,
    )
    assert cfg.llm.call_timeout_s == 180.0
    assert cfg.llm.call_retries == 2
    assert cfg.gateway.reply_timeout_s == 600.0


def test_resolve_config_defaults_when_no_file(paths):
    assert resolve_config({}, paths).llm.provider == "gemini"


def test_config_yaml_accepts_a_json_document(paths):
    """YAML is a JSON superset, so a config.yaml written as JSON still resolves."""
    paths.config_yaml.parent.mkdir(parents=True, exist_ok=True)
    paths.config_yaml.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "aggregate_model": "claude-haiku",
                },
                "memory": {"aggregate_every_n_turns": 9},
            }
        )
    )
    cfg = resolve_config({}, paths)
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-sonnet-4-6"
    assert cfg.llm.aggregate_model == "claude-haiku"
    assert cfg.memory.aggregate_every_n_turns == 9


def test_env_overrides_the_file_across_sections(paths):
    write_yaml(paths.config_yaml, {"llm": {"model": "from-file"}})
    cfg = resolve_config(
        {
            "AG2ASSISTANT_MODEL": "from-env",
            "AG2ASSISTANT_STREAMING": "false",
            "AG2ASSISTANT_SANDBOX": "docker",
            "AG2ASSISTANT_AGGREGATE_EVERY_N": "7",
        },
        paths,
    )
    assert cfg.llm.model == "from-env"  # env beats the file
    assert cfg.llm.streaming is False
    assert cfg.tools.sandbox == "docker"
    assert cfg.memory.aggregate_every_n_turns == 7


def test_malformed_config_yaml_reads_as_empty(paths):
    paths.config_yaml.parent.mkdir(parents=True, exist_ok=True)
    paths.config_yaml.write_text("{{{ not yaml")
    assert resolve_config({}, paths).llm.provider == "gemini"


def test_resolve_config_reads_yaml(paths):
    write_yaml(paths.config_yaml, {"agent": {"name": "custom"}, "llm": {"model": "my-model"}})
    cfg = resolve_config({}, paths)
    assert cfg.agent.name == "custom"
    assert cfg.llm.model == "my-model"


def test_malformed_yaml_falls_back_to_defaults(paths):
    paths.config_yaml.parent.mkdir(parents=True, exist_ok=True)
    paths.config_yaml.write_text("[unclosed")
    assert resolve_config({}, paths).agent.name == "ag2-assistant"


def test_config_file_is_yaml(paths):
    assert paths.config_yaml.name == "config.yaml"


def test_data_dir_in_the_file_cannot_move_the_resolved_layout(paths, tmp_path):
    """Paths already resolved the layout; a stale ``data_dir`` key must not split it."""
    write_yaml(paths.config_yaml, {"data_dir": str(tmp_path / "elsewhere")})
    cfg = resolve_config({}, paths)
    assert cfg.data_dir == paths.root
    assert cfg.root_dir == paths.root


def test_load_config_composes_resolve_with_the_real_environment():
    """The entry point glues the pure resolve onto os.environ + Path.home()."""
    cfg = load_config()
    expected = resolve_config(os.environ, Paths.from_env(os.environ, Path.home()))
    assert cfg.llm.provider == expected.llm.provider
    assert cfg.data_dir == expected.data_dir


def test_workspace_dir_default_and_env(tmp_path):
    cfg = resolve_config({}, Paths.from_env({}, tmp_path))
    assert cfg.workspace_dir == tmp_path / "Documents" / "AG2 Assistant"
    env = {"AG2ASSISTANT_WORKSPACE": "/tmp/custom-ws"}
    assert resolve_config(env, Paths.from_env(env, tmp_path)).workspace_dir == Path(
        "/tmp/custom-ws"
    )


def _meta(pid="work"):
    return ProfileMeta(
        id=pid,
        name=pid.title(),
        accent="#109e91",
        created="2026-01-01T00:00:00Z",
    )


def test_with_profile_reroots_paths_under_the_profile(paths):
    scoped = resolve_config({}, paths).with_profile(_meta())
    assert scoped.data_dir == paths.profile_dir("work")
    assert scoped.skills_dir == paths.profile_dir("work") / "skills"
    assert scoped.workspace_dir == paths.profile_dir("work") / "workspace"
    assert scoped.root_dir == paths.root  # global files stay at the root


def test_profile_overlay_overrides_global(paths):
    cfg = Config.for_paths(paths)
    write_yaml(
        paths.profile_dir("work") / "config.yaml",
        {"llm": {"model": "overlay-model"}, "gateway": {"reply_timeout_s": 480}},
    )
    prof = cfg.with_profile(_meta())
    assert prof.llm.model == "overlay-model"
    assert prof.llm.provider == cfg.llm.provider  # untouched fields inherit the global
    assert prof.gateway.reply_timeout_s == 480
    assert cfg.llm.model != "overlay-model"  # the base config is not mutated


def test_env_still_wins_over_profile_overlay(paths):
    env = {"AG2ASSISTANT_MODEL": "env-model"}
    cfg = resolve_config(env, paths)
    write_yaml(paths.profile_dir("work") / "config.yaml", {"llm": {"model": "overlay-model"}})
    prof = cfg.with_profile(_meta(), env=env)
    assert prof.llm.model == "env-model"
    # paths are not clobbered by the env re-apply
    assert prof.data_dir == paths.profile_dir("work")


def test_malformed_overlay_section_is_skipped(paths):
    cfg = Config.for_paths(paths)
    pdir = paths.profile_dir("work")
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text("llm:\n  call_retries: not-a-number\nagent:\n  name: ok\n")
    prof = cfg.with_profile(_meta())
    assert prof.llm.call_retries == 2  # bad section skipped wholesale
    assert prof.agent.name == "ok"  # good section still applies


def test_with_profile_deep_copy_isolates_nested_models(paths):
    base = Config.for_paths(paths)
    derived = base.with_profile(_meta())
    derived.llm.model = "changed"
    assert base.llm.model != "changed"  # deep copy, not a shared reference


# ---- per-profile LLM Active override (ADR 0015) -------------------------------
# The effective Active resolves env pin > profile override > install-wide Active >
# env fallback, at the config-load / active-derivation seam (with_profile).


def _write_override(paths, pid, cid):
    write_yaml(paths.profile_dir(pid) / "config.yaml", {"llm_active_override": cid})


def _two_shared_configs(paths):
    """Two shared install-wide LLM configs (anthropic active, openai the override
    target). Returns (a, b)."""
    store = LlmConfigStore(paths)
    a = store.save_config({"name": "A", "type": "anthropic", "model": "claude-x"})
    b = store.save_config({"name": "B", "type": "openai", "model": "gpt-x"})
    store.set_active(a["id"])
    return a, b


def test_profile_llm_override_absent_inherits_install_active(paths):
    _two_shared_configs(paths)
    prof = resolve_config({}, paths).with_profile(_meta())  # no override written
    assert prof.llm.provider == "anthropic"
    assert prof.llm.model == "claude-x"


def test_profile_llm_override_wins_over_install_active(paths):
    _a, b = _two_shared_configs(paths)
    _write_override(paths, "work", b["id"])
    prof = resolve_config({}, paths).with_profile(_meta())
    assert prof.llm.provider == "openai"
    assert prof.llm.model == "gpt-x"


def test_env_pin_wins_over_profile_llm_override(paths):
    _a, b = _two_shared_configs(paths)
    env = {"AG2ASSISTANT_LLM_PROVIDER": "gemini", "AG2ASSISTANT_MODEL": "gemini-pinned"}
    _write_override(paths, "work", b["id"])
    prof = resolve_config(env, paths).with_profile(_meta(), env=env)
    assert prof.llm.provider == "gemini"  # env pin wins last, over the override
    assert prof.llm.model == "gemini-pinned"


def test_dangling_profile_llm_override_falls_back_to_install_active(paths):
    _two_shared_configs(paths)
    _write_override(paths, "work", "c_deleted_ghost")  # points at nothing
    prof = resolve_config({}, paths).with_profile(_meta())  # no error; degrades silently
    assert prof.llm.provider == "anthropic"
    assert prof.llm.model == "claude-x"


def test_profile_llm_override_isolated_between_profiles(paths):
    a, b = _two_shared_configs(paths)
    _write_override(paths, "work", b["id"])  # only Work overrides
    cfg = resolve_config({}, paths)
    assert cfg.with_profile(_meta("work")).llm.model == "gpt-x"  # its own override
    assert cfg.with_profile(_meta("home")).llm.model == "claude-x"  # inherits Active
    assert LlmConfigStore(paths).active_id() == a["id"]  # install-wide Active never moved


def test_yaml_roundtrip_helpers(tmp_path):
    p = tmp_path / "nested" / "config.yaml"
    write_yaml(p, {"a": 1, "b": {"c": "х"}})  # unicode survives
    assert read_yaml(p) == {"a": 1, "b": {"c": "х"}}
    assert read_yaml(tmp_path / "absent.yaml") == {}
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n")
    assert read_yaml(tmp_path / "list.yaml") == {}  # non-mapping reads as empty


def test_model_config_gemini_and_aggregate_override(paths):
    cfg = Config.for_paths(paths, llm=LLMConfig(provider="gemini", model="gemini-3.6-flash"))
    mc = model_config(cfg)
    assert type(mc).__name__ == "GeminiConfig"
    assert mc.model == "gemini-3.6-flash"
    assert mc.streaming is True
    # aggregate override picks a different (cheaper) model, same provider
    mc2 = model_config(cfg, "gemini-2.5-flash")
    assert type(mc2).__name__ == "GeminiConfig"
    assert mc2.model == "gemini-2.5-flash"
    assert mc2.streaming is True


def test_model_config_respects_streaming_disabled(paths):
    cfg = Config.for_paths(
        paths, llm=LLMConfig(provider="gemini", model="gemini-3.6-flash", streaming=False)
    )
    assert model_config(cfg).streaming is False


def test_model_config_dispatches_anthropic(paths):
    pytest.importorskip("anthropic")  # needs `pip install ag2[anthropic]`

    cfg = Config.for_paths(
        paths,
        llm=LLMConfig(
            provider="anthropic", model="claude-sonnet-4-6", api_key_env="ANTHROPIC_API_KEY"
        ),
        secret_env={"ANTHROPIC_API_KEY": "x"},
    )
    assert type(model_config(cfg)).__name__ == "AnthropicConfig"
