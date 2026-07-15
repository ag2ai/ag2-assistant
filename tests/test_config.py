"""Tests for AG2 Assistant configuration."""

import json
from pathlib import Path

from assistant.config import AgentConfig, Config, LLMConfig, load_config


def test_default_config():
    config = Config()
    assert config.llm.provider == "gemini"
    assert config.llm.model.startswith("gemini")
    assert config.llm.streaming is True
    assert config.agent.name == "ag2-assistant"
    assert config.data_dir == Path.home() / ".ag2assistant"


def test_default_timeout_and_silence_thresholds():
    llm = Config().llm
    assert llm.call_timeout_s == 180.0
    assert llm.call_retries == 2
    assert llm.silence_alert_s == 300.0
    assert llm.silence_halt_s == 900.0


def test_timeout_and_silence_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("AG2ASSISTANT_LLM_TIMEOUT", "45")
    monkeypatch.setenv("AG2ASSISTANT_LLM_RETRIES", "5")
    monkeypatch.setenv("AG2ASSISTANT_SILENCE_ALERT", "120")
    monkeypatch.setenv("AG2ASSISTANT_SILENCE_HALT", "600")
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.llm.call_timeout_s == 45.0
    assert cfg.llm.call_retries == 5
    assert cfg.llm.silence_alert_s == 120.0
    assert cfg.llm.silence_halt_s == 600.0


def test_bad_timeout_env_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setenv("AG2ASSISTANT_LLM_TIMEOUT", "not-a-number")
    monkeypatch.setenv("AG2ASSISTANT_LLM_RETRIES", "not-a-number")
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.llm.call_timeout_s == 180.0
    assert cfg.llm.call_retries == 2


def test_custom_llm_config():
    llm = LLMConfig(provider="openai", model="gpt-4o", api_key_env="OPENAI_API_KEY")
    config = Config(llm=llm)
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o"


def test_custom_agent_config():
    agent = AgentConfig(name="test-agent", system_prompt="You are a test agent.")
    config = Config(agent=agent)
    assert config.agent.name == "test-agent"
    assert "test agent" in config.agent.system_prompt


def test_load_config_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.llm.provider == "gemini"


def test_load_config_reads_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
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
    cfg = load_config(p)
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-sonnet-4-6"
    assert cfg.llm.aggregate_model == "claude-haiku"
    assert cfg.memory.aggregate_every_n_turns == 9


def test_env_overrides_json(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"llm": {"model": "from-json"}}))
    monkeypatch.setenv("AG2ASSISTANT_MODEL", "from-env")
    monkeypatch.setenv("AG2ASSISTANT_STREAMING", "false")
    monkeypatch.setenv("AG2ASSISTANT_SANDBOX", "docker")
    monkeypatch.setenv("AG2ASSISTANT_AGGREGATE_EVERY_N", "7")
    cfg = load_config(p)
    assert cfg.llm.model == "from-env"  # env beats json
    assert cfg.llm.streaming is False
    assert cfg.tools.sandbox == "docker"
    assert cfg.memory.aggregate_every_n_turns == 7


def test_malformed_json_falls_back_to_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not valid json ")
    cfg = load_config(p)
    assert cfg.llm.provider == "gemini"


def _meta(tmp_path, pid="work"):
    from assistant.profiles import ProfileMeta

    return ProfileMeta(
        id=pid,
        name=pid.title(),
        accent="#109e91",
        created="2026-01-01T00:00:00Z",
    )


def test_profile_overlay_overrides_global(tmp_path):
    cfg = Config(root_dir=tmp_path, data_dir=tmp_path)
    pdir = tmp_path / "profiles" / "work"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text("llm:\n  model: overlay-model\nvoice:\n  gemini: Puck\n")
    prof = cfg.with_profile(_meta(tmp_path))
    assert prof.llm.model == "overlay-model"
    assert prof.llm.provider == cfg.llm.provider  # untouched fields inherit the global
    assert cfg.llm.model != "overlay-model"  # the base config is not mutated


def test_env_still_wins_over_profile_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv("AG2ASSISTANT_MODEL", "env-model")
    cfg = load_config(tmp_path / "absent.yaml")
    cfg.root_dir = tmp_path
    pdir = tmp_path / "profiles" / "work"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text("llm:\n  model: overlay-model\n")
    prof = cfg.with_profile(_meta(tmp_path))
    assert prof.llm.model == "env-model"
    assert prof.data_dir == tmp_path / "profiles" / "work"  # paths not clobbered by env re-apply


def test_malformed_overlay_section_is_skipped(tmp_path):
    cfg = Config(root_dir=tmp_path, data_dir=tmp_path)
    pdir = tmp_path / "profiles" / "work"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text("llm:\n  call_retries: not-a-number\nagent:\n  name: ok\n")
    prof = cfg.with_profile(_meta(tmp_path))
    assert prof.llm.call_retries == 2  # bad section skipped wholesale
    assert prof.agent.name == "ok"  # good section still applies


def test_load_config_reads_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("agent:\n  name: custom\nllm:\n  model: my-model\n")
    cfg = load_config(cfg_file)
    assert cfg.agent.name == "custom"
    assert cfg.llm.model == "my-model"


def test_malformed_yaml_falls_back_to_defaults(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("[unclosed")
    cfg = load_config(cfg_file)
    assert cfg.agent.name == "ag2-assistant"


def test_default_config_path_is_yaml():
    from assistant.config import default_config_path

    assert default_config_path().name == "config.yaml"


def test_yaml_roundtrip_helpers(tmp_path):
    from assistant.config import read_yaml, write_yaml

    p = tmp_path / "nested" / "config.yaml"
    write_yaml(p, {"a": 1, "b": {"c": "х"}})  # unicode survives
    assert read_yaml(p) == {"a": 1, "b": {"c": "х"}}
    assert read_yaml(tmp_path / "absent.yaml") == {}
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n")
    assert read_yaml(tmp_path / "list.yaml") == {}  # non-mapping reads as empty


def test_model_config_gemini_and_aggregate_override():
    from assistant.agent import model_config

    cfg = Config(llm=LLMConfig(provider="gemini", model="gemini-3.5-flash"))
    mc = model_config(cfg)
    assert type(mc).__name__ == "GeminiConfig"
    assert mc.model == "gemini-3.5-flash"
    assert mc.streaming is True
    # aggregate override picks a different (cheaper) model, same provider
    mc2 = model_config(cfg, "gemini-2.5-flash")
    assert type(mc2).__name__ == "GeminiConfig"
    assert mc2.model == "gemini-2.5-flash"
    assert mc2.streaming is True


def test_model_config_respects_streaming_disabled():
    from assistant.agent import model_config

    cfg = Config(llm=LLMConfig(provider="gemini", model="gemini-3.5-flash", streaming=False))
    assert model_config(cfg).streaming is False


def test_model_config_dispatches_anthropic(monkeypatch):
    import pytest

    pytest.importorskip("anthropic")  # needs `pip install ag2[anthropic]`
    from assistant.agent import model_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    cfg = Config(
        llm=LLMConfig(
            provider="anthropic", model="claude-sonnet-4-6", api_key_env="ANTHROPIC_API_KEY"
        )
    )
    assert type(model_config(cfg)).__name__ == "AnthropicConfig"


def test_workspace_dir_default_and_env(monkeypatch):
    monkeypatch.delenv("AG2ASSISTANT_WORKSPACE", raising=False)
    cfg = load_config()
    assert cfg.workspace_dir == Path.home() / "Documents" / "AG2 Assistant"
    monkeypatch.setenv("AG2ASSISTANT_WORKSPACE", "/tmp/custom-ws")
    assert load_config().workspace_dir == Path("/tmp/custom-ws")
