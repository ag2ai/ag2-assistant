"""Provider-aware image generation tool wiring (the model call itself is live)."""

import json
import time

import pytest

from assistant import codex_auth
from assistant.config import Config
from assistant.llm_configs import LlmConfigStore
from assistant.secrets import SecretStore
from assistant.tools.image_gen import (
    _image_agent,
    _workspace_file_url,
    build_image_tool,
    image_available,
)


def _cfg(provider: str, paths) -> Config:
    c = Config.for_paths(paths)
    c.llm.provider = provider
    c.secret_env = {"GEMINI_API_KEY": "test-gemini", "OPENAI_API_KEY": "test-openai"}
    return c


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_images_need_credentials(paths, provider):
    config = _cfg(provider, paths)
    config.secret_env = {}
    assert not image_available(config)
    assert _image_agent(config) is None


def test_subscription_images_need_sign_in(paths):
    store = LlmConfigStore(paths)
    entry = store.save_config({"name": "Sub", "type": "openai_subscription", "model": "gpt"})
    store.set_active(entry["id"])
    assert not image_available(_cfg("openai", paths))


def test_images_use_the_resolved_chat_model_and_its_secret(paths):
    store = LlmConfigStore(paths)
    active = store.save_config({"name": "Global", "type": "gemini", "model": "global"})
    store.set_active(active["id"])
    secret = SecretStore(paths).create_secret("Chat key", "chat-key")
    chat = store.save_config(
        {"name": "Chat", "type": "openai_responses", "model": "chat", "secret_id": secret["id"]}
    )
    config = _cfg("gemini", paths)
    config.secret_env = {}
    store.derive_onto(config, chat)
    agent = _image_agent(config)
    assert agent is not None
    assert agent.config.model == "chat"
    assert agent.config.api_key == "chat-key"
    incompatible = store.save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    store.derive_onto(config, incompatible)
    assert not image_available(config)


@pytest.mark.parametrize("ctype", ["openai", "openai_responses"])
def test_custom_openai_endpoint_does_not_offer_native_images(paths, ctype):
    config = _cfg("openai", paths)
    config.llm.config_type = ctype
    config.llm.provider_options = {"openai": {"base_url": "http://localhost:8000/v1"}}
    assert not image_available(config)


def test_image_availability_honors_the_environment_model_pin(paths):
    store = LlmConfigStore(paths)
    entry = store.save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    store.set_active(entry["id"])
    config = _cfg("gemini", paths)
    config.llm.env_pinned = True
    assert image_available(config)


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


def test_image_agent_follows_active_config_only(paths):
    """Images follow the SELECTED configuration: an active non-capable config means
    images are unavailable even when a capable one sits in the list; switching the
    active config to it enables images."""
    active = LlmConfigStore(paths).save_config(
        {"name": "Claude", "type": "anthropic", "model": "cl"}
    )
    LlmConfigStore(paths).set_active(active["id"])
    gem = LlmConfigStore(paths).save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert _image_agent(_cfg("anthropic", paths)) is None  # active can't do images → off
    LlmConfigStore(paths).set_active(gem["id"])
    assert _image_agent(_cfg("anthropic", paths)) is not None  # selected config powers images


def test_image_agent_none_when_store_has_no_capable_entry(paths):
    LlmConfigStore(paths).save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    LlmConfigStore(paths).save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    assert _image_agent(_cfg("gemini", paths)) is None  # store configured, none image-capable


def test_image_agent_subscription_routes_to_chatgpt_backend(paths):
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
