"""API-key secrets store, settings LLM selection, and model_config provider mapping."""

import json
import os
import stat

import pytest

from assistant import secrets
from assistant.agent import cheap_model, model_config
from assistant.config import Config, data_dir, load_config
from assistant.gateway.core import Gateway


def test_secrets_set_status_clear_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # data_dir → tmp/.ag2assistant
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # empty = "no key" (load_dotenv won't override)

    assert secrets.status()["anthropic"]["set"] is False

    assert secrets.set_key("anthropic", "sk-ant-secret-9999")
    st = secrets.status()["anthropic"]
    assert st["set"] is True and st["hint"] == "…9999"  # only the last 4, never raw
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-9999"  # loaded into env

    # file is 0600
    p = secrets._path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600

    assert secrets.clear("anthropic")
    assert secrets.status()["anthropic"]["set"] is False

    assert secrets.set_key("bogus", "x") is False  # unknown provider


def test_ollama_base_url(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert secrets.set_key("ollama", "http://host:1234")
    secrets.load_into_env()
    assert os.environ["OLLAMA_BASE_URL"] == "http://host:1234"
    assert secrets.status()["ollama"]["base_url"] == "http://host:1234"


def test_load_config_no_longer_overlays_settings(monkeypatch, tmp_path):
    """A per-profile settings llm block is NOT consulted by load_config() — the
    assistant model is the install-wide named-config store now. load_config() derives
    only defaults ← config.yaml ← active llm config ← env; with no store it stays on
    the flat gemini defaults, ignoring any stray profile-settings llm block."""

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AG2ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AG2ASSISTANT_MODEL", raising=False)

    # A stray legacy llm block written straight into a settings.json is ignored.
    settings_file = data_dir() / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({"llm": {"provider": "anthropic", "model": "claude-x"}}))
    cfg = load_config()
    assert cfg.llm.provider == "gemini"  # default, settings NOT overlaid
    assert cfg.llm.model.startswith("gemini")

    # explicit env still applies
    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "openai")
    assert load_config().llm.provider == "openai"


@pytest.mark.asyncio
async def test_gateway_reload_swaps_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    g = Gateway(memory=False, persist=False)
    await g.start()
    first = g._agent
    assert first is not None
    await g.reload()  # reference-swap
    assert g._agent is not None and g._agent is not first


def test_model_config_key_env_by_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    cfg = Config()
    cfg.llm.provider = "openai"
    cfg.llm.model = "gpt-x"
    mc = model_config(cfg)
    assert getattr(mc, "api_key", None) == "sk-openai-test"  # picked OPENAI_API_KEY by provider


def test_model_config_provider_options_openai_compatible(monkeypatch, tmp_path):
    """base_url in the openai advanced options points the client at an
    OpenAI-compatible server AND defaults to the Chat Completions API (those
    servers rarely implement /v1/responses); "api": "responses" pins it back."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")

    cfg = Config()
    cfg.llm.provider = "openai"
    cfg.llm.model = "gemma-4-31B-it-qat"
    cfg.llm.provider_options = {"openai": {"base_url": "http://192.168.0.55:8080/v1"}}
    mc = model_config(cfg)
    assert type(mc).__name__ == "OpenAIConfig"  # chat completions for compat servers
    assert mc.base_url == "http://192.168.0.55:8080/v1"
    assert mc.model == "gemma-4-31B-it-qat"

    cfg.llm.provider_options["openai"]["api"] = "responses"
    mc2 = model_config(cfg)
    assert type(mc2).__name__ == "OpenAIResponsesConfig"
    assert mc2.base_url == "http://192.168.0.55:8080/v1"

    cfg.llm.provider_options["openai"]["api"] = "grpc"  # unknown surface → clear error
    with pytest.raises(ValueError):
        model_config(cfg)

    # a typo'd kwarg raises at construction (what the endpoint's dry-run catches)
    cfg.llm.provider_options["openai"] = {"bogus_kwarg": 1}
    with pytest.raises(TypeError):
        model_config(cfg)


def test_provider_options_suppress_default_aggregate_model(monkeypatch, tmp_path):
    """With a custom base_url the cheap-tier default (an OpenAI model name) would
    not exist on the server — fall back to the main model, like Ollama does."""
    monkeypatch.setenv("HOME", str(tmp_path))

    cfg = Config()
    cfg.llm.provider = "openai"
    assert cheap_model(cfg) == "gpt-5-mini"  # normal OpenAI keeps the cheap default
    cfg.llm.provider_options = {"openai": {"base_url": "http://192.168.0.55:8080/v1"}}
    assert cheap_model(cfg) is None  # custom server → reuse the main model
    cfg.llm.aggregate_model = "explicit-model"
    assert cheap_model(cfg) == "explicit-model"  # explicit choice always wins
