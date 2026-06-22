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
    assert "image" in cfg.model  # an image model, e.g. gemini-3.1-flash-image


def test_build_image_tool_is_named_generate_image(tmp_path):
    tool = build_image_tool(_cfg("gemini"), tmp_path)
    assert tool.name == "generate_image"
