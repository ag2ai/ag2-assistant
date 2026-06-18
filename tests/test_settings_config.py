"""API-key secrets store, settings LLM selection, and model_config provider mapping."""

import os
import stat

import pytest


def test_secrets_set_status_clear_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))      # data_dir → tmp/.agclaw
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")    # empty = "no key" (load_dotenv won't override)
    from assistant import secrets

    assert secrets.status()["anthropic"]["set"] is False

    assert secrets.set_key("anthropic", "sk-ant-secret-9999")
    st = secrets.status()["anthropic"]
    assert st["set"] is True and st["hint"] == "…9999"   # only the last 4, never raw
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-9999"  # loaded into env

    # file is 0600
    p = secrets._path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600

    assert secrets.clear("anthropic")
    assert secrets.status()["anthropic"]["set"] is False

    assert secrets.set_key("bogus", "x") is False        # unknown provider


def test_ollama_base_url(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant import secrets

    assert secrets.set_key("ollama", "http://host:1234")
    secrets.load_into_env()
    assert os.environ["OLLAMA_BASE_URL"] == "http://host:1234"
    assert secrets.status()["ollama"]["base_url"] == "http://host:1234"


def test_settings_llm_overrides_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGCLAW_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AGCLAW_MODEL", raising=False)
    from assistant import settings
    from assistant.config import load_config

    settings.set_llm(provider="anthropic", model="claude-x")
    cfg = load_config()
    assert cfg.llm.provider == "anthropic" and cfg.llm.model == "claude-x"

    # explicit env still wins over the UI setting
    monkeypatch.setenv("AGCLAW_LLM_PROVIDER", "gemini")
    assert load_config().llm.provider == "gemini"


@pytest.mark.asyncio
async def test_gateway_reload_swaps_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant.gateway.core import Gateway

    g = Gateway(memory=False, persist=False)
    await g.start()
    first = g._agent
    assert first is not None
    await g.reload()                 # reference-swap
    assert g._agent is not None and g._agent is not first


def test_model_config_key_env_by_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant.agent import model_config
    from assistant.config import Config

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    cfg = Config()
    cfg.llm.provider = "openai"
    cfg.llm.model = "gpt-x"
    mc = model_config(cfg)
    assert getattr(mc, "api_key", None) == "sk-openai-test"   # picked OPENAI_API_KEY by provider
