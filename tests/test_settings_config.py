"""API-key secrets store, settings LLM selection, and model_config provider mapping."""

import pytest

from assistant.agent import cheap_model, model_config
from assistant.config import Config
from assistant.secrets import SecretStore


def test_ollama_base_url(paths):
    store = SecretStore(paths)
    assert store.set_key("ollama", "http://host:1234")
    assert store.env_overlay()["OLLAMA_BASE_URL"] == "http://host:1234"
    assert store.status(store.env_overlay())["ollama"]["base_url"] == "http://host:1234"


def test_model_config_key_env_by_provider(paths):
    cfg = Config.for_paths(paths, secret_env={"OPENAI_API_KEY": "sk-openai-test"})
    cfg.llm.provider = "openai"
    cfg.llm.model = "gpt-x"
    mc = model_config(cfg)
    assert getattr(mc, "api_key", None) == "sk-openai-test"  # picked OPENAI_API_KEY by provider


def test_model_config_provider_options_openai_compatible(paths):
    """base_url in the openai advanced options points the client at an
    OpenAI-compatible server AND defaults to the Chat Completions API (those
    servers rarely implement /v1/responses); "api": "responses" pins it back."""
    cfg = Config.for_paths(paths)
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


def test_provider_options_suppress_default_aggregate_model(paths):
    """With a custom base_url the cheap-tier default (an OpenAI model name) would
    not exist on the server — fall back to the main model, like Ollama does."""
    cfg = Config.for_paths(paths)
    cfg.llm.provider = "openai"
    assert cheap_model(cfg) == "gpt-5-mini"  # normal OpenAI keeps the cheap default
    cfg.llm.provider_options = {"openai": {"base_url": "http://192.168.0.55:8080/v1"}}
    assert cheap_model(cfg) is None  # custom server → reuse the main model
    cfg.llm.aggregate_model = "explicit-model"
    assert cheap_model(cfg) == "explicit-model"  # explicit choice always wins
