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


def test_image_agent_resolves_capable_entry_from_store(monkeypatch, tmp_path):
    """With named configs present, _image_agent picks an image-capable entry
    (gemini here) even when the active/flat provider (anthropic) can't do images."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant import llm_configs

    active = llm_configs.save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    llm_configs.set_active(active["id"])
    llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})
    # config.llm.provider is anthropic, but the store yields the gemini entry
    assert _image_agent(_cfg("anthropic")) is not None


def test_image_agent_none_when_store_has_no_capable_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from assistant import llm_configs

    llm_configs.save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    llm_configs.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    assert _image_agent(_cfg("gemini")) is None  # store configured, none image-capable
