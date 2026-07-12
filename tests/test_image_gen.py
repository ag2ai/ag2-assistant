"""Provider-aware image generation tool wiring (the model call itself is live)."""

from assistant.config import Config
from assistant.tools.image_gen import _image_agent, build_image_tool


def _cfg(provider: str) -> Config:
    c = Config()
    c.llm.provider = provider
    return c


def test_image_agent_built_for_gemini_and_openai():
    assert _image_agent(_cfg("gemini")) is not None
    assert _image_agent(_cfg("openai")) is not None


def test_image_agent_none_for_providers_without_image_models():
    assert _image_agent(_cfg("anthropic")) is None
    assert _image_agent(_cfg("ollama")) is None


def test_gemini_image_agent_requests_image_modality():
    agent = _image_agent(_cfg("gemini"))
    cfg = agent.config
    assert "IMAGE" in (cfg.response_modalities or [])
    assert "image" in cfg.model  # an image model, e.g. gemini-3.1-flash-lite-image


def test_build_image_tool_is_named_generate_image(tmp_path):
    tool = build_image_tool(_cfg("gemini"), tmp_path)
    assert tool.name == "generate_image"


def test_image_agent_follows_active_config_only(monkeypatch, tmp_path):
    """Images follow the SELECTED configuration: an active non-capable config means
    images are unavailable even when a capable one sits in the list; switching the
    active config to it enables images."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant import llm_configs

    active = llm_configs.save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    llm_configs.set_active(active["id"])
    gem = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert _image_agent(_cfg("anthropic")) is None  # active can't do images → off
    llm_configs.set_active(gem["id"])
    assert _image_agent(_cfg("anthropic")) is not None  # selected config powers images


def test_image_agent_none_when_store_has_no_capable_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant import llm_configs

    llm_configs.save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    llm_configs.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    assert _image_agent(_cfg("gemini")) is None  # store configured, none image-capable


def test_image_agent_subscription_routes_to_chatgpt_backend(monkeypatch, tmp_path):
    """An active ChatGPT-subscription config powers image generation through the
    ChatGPT backend with the OAuth token — streaming forced on, storage off, and
    the native image tool attached (same rules as model_config's branch)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant import codex_auth, llm_configs

    sub = llm_configs.save_config(
        {"name": "Sub", "type": "openai_subscription", "model": "gpt-5.6-luna"}
    )
    llm_configs.set_active(sub["id"])
    monkeypatch.setattr(
        codex_auth,
        "creds_best_effort",
        lambda: codex_auth.Creds(access_token="TOK", account_id="acc"),
    )
    agent = _image_agent(_cfg("openai"))
    assert agent is not None
    pc = agent.config  # the constructed OpenAIResponsesConfig
    assert pc.base_url == codex_auth.BACKEND_BASE
    assert pc.api_key == "TOK"
    assert pc.streaming is True and pc.store is False
