"""Provider-aware image generation tool wiring (the model call itself is live)."""

import json
import time

from assistant import codex_auth
from assistant.config import Config
from assistant.llm_configs import LlmConfigStore
from assistant.tools.image_gen import _image_agent, _workspace_file_url, build_image_tool


def _cfg(provider: str, paths) -> Config:
    c = Config.for_paths(paths)
    c.llm.provider = provider
    return c


def test_image_agent_built_for_gemini_and_openai(paths):
    assert _image_agent(_cfg("gemini", paths)) is not None
    assert _image_agent(_cfg("openai", paths)) is not None


def test_image_agent_none_for_providers_without_image_models(paths):
    assert _image_agent(_cfg("anthropic", paths)) is None
    assert _image_agent(_cfg("ollama", paths)) is None


def test_gemini_image_agent_requests_image_modality(paths):
    agent = _image_agent(_cfg("gemini", paths))
    cfg = agent.config
    assert "IMAGE" in (cfg.response_modalities or [])
    assert "image" in cfg.model  # an image model, e.g. gemini-3.1-flash-lite-image


def test_build_image_tool_is_named_generate_image(paths, tmp_path):
    tool = build_image_tool(_cfg("gemini", paths), tmp_path)
    assert tool.name == "generate_image"


def test_workspace_file_url_is_profile_scoped_and_path_encoded(paths, tmp_path):
    config = _cfg("gemini", paths)
    config.root_dir = tmp_path
    config.data_dir = tmp_path / "profiles" / "work"

    assert _workspace_file_url(config, "images/sunrise & lake.jpg") == (
        "/api/p/work/files/raw?path=images%2Fsunrise%20%26%20lake.jpg"
    )


def test_image_agent_follows_active_config_only(paths, monkeypatch, tmp_path):
    """Images follow the SELECTED configuration: an active non-capable config means
    images are unavailable even when a capable one sits in the list; switching the
    active config to it enables images."""
    monkeypatch.setenv("HOME", str(tmp_path))

    active = LlmConfigStore(paths).save_config(
        {"name": "Claude", "type": "anthropic", "model": "cl"}
    )
    LlmConfigStore(paths).set_active(active["id"])
    gem = LlmConfigStore(paths).save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert _image_agent(_cfg("anthropic", paths)) is None  # active can't do images → off
    LlmConfigStore(paths).set_active(gem["id"])
    assert _image_agent(_cfg("anthropic", paths)) is not None  # selected config powers images


def test_image_agent_none_when_store_has_no_capable_entry(paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    LlmConfigStore(paths).save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    LlmConfigStore(paths).save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    assert _image_agent(_cfg("gemini", paths)) is None  # store configured, none image-capable


def test_image_agent_subscription_routes_to_chatgpt_backend(paths, monkeypatch, tmp_path):
    """An active ChatGPT-subscription config powers image generation through the
    ChatGPT backend with the OAuth token — streaming forced on, storage off, and
    the native image tool attached (same rules as model_config's branch)."""
    sub = LlmConfigStore(paths).save_config(
        {"name": "Sub", "type": "openai_subscription", "model": "gpt-5.6-luna"}
    )
    LlmConfigStore(paths).set_active(sub["id"])
    # A real signed-in session on disk, rather than a patched auth module.
    paths.codex_tokens.parent.mkdir(parents=True, exist_ok=True)
    paths.codex_tokens.write_text(
        json.dumps(
            {
                "access_token": "TOK",
                "refresh_token": "RX",
                "account_id": "acc",
                "expires_at": time.time() + 3600,
            }
        )
    )
    agent = _image_agent(_cfg("openai", paths))
    assert agent is not None
    pc = agent.config  # the constructed OpenAIResponsesConfig
    assert pc.base_url == codex_auth.BACKEND_BASE
    assert pc.api_key == "TOK"
    assert pc.streaming is True and pc.store is False
