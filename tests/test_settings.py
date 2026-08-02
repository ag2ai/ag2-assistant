"""Provider-aware realtime voice settings + provider registry.

Settings are per-profile: each test builds a `Settings(path)` bound to a tmp file,
so there is no shared global settings state.
"""

import pytest
import yaml

from assistant import voice_providers
from assistant.config import resolve_config
from assistant.settings import Settings, profile_settings


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "config.yaml")


def _pinned(tmp_path, provider: str) -> Settings:
    """The same profile's settings, read through a given install-wide voice pin."""
    return Settings(tmp_path / "config.yaml", voice_provider=provider)


def test_settings_preserve_overlay_sections(tmp_path):

    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  model: overlay\n")
    s = Settings(path)
    s.set_focuses(["research"])
    data = yaml.safe_load(path.read_text())
    assert data["llm"]["model"] == "overlay"  # settings writes keep the Config overlay
    assert data["focuses"] == ["research"]


def test_profile_settings_accessor(tmp_path):

    s = profile_settings(tmp_path)
    assert s._path == tmp_path / "config.yaml"


def test_voice_provider_pin(tmp_path):
    """With nothing persisted, the install-wide pin (AG2ASSISTANT_VOICE_PROVIDER,
    resolved into Config at the boundary) decides."""
    assert _pinned(tmp_path, "").voice_provider() == "gemini"  # unpinned → default
    assert _pinned(tmp_path, "openai").voice_provider() == "openai"
    assert _pinned(tmp_path, "OpenAI").voice_provider() == "openai"  # case-insensitive
    assert _pinned(tmp_path, "bogus").voice_provider() == "gemini"  # unknown → default


def test_persisted_voice_provider_wins_over_the_pin(tmp_path):
    # A profile's persisted choice takes precedence over the install-wide pin.
    settings = _pinned(tmp_path, "gemini")
    assert settings.set_voice_provider("openai") is True
    assert settings.voice_provider() == "openai"
    assert settings.set_voice_provider("bogus") is False


def test_the_pin_reaches_settings_from_the_environment(paths):
    """End to end: the env var lands on Config at the boundary, and profile_settings
    hands it to the store."""
    cfg = resolve_config({"AG2ASSISTANT_VOICE_PROVIDER": "openai"}, paths)
    assert cfg.voice_provider == "openai"
    assert profile_settings(cfg.data_dir, voice_provider=cfg.voice_provider).voice_provider() == (
        "openai"
    )


def test_registry_has_both_providers():
    assert set(voice_providers.names()) == {"gemini", "openai"}
    assert voice_providers.get("gemini").default_voice == "Puck"
    assert voice_providers.get("openai").default_voice == "marin"
    assert voice_providers.get("bogus").name == "gemini"  # unknown → default


def test_voices_for_each_provider(settings):
    assert settings.voices_for("gemini") is voice_providers.get("gemini").voices
    assert settings.voices_for("openai") is voice_providers.get("openai").voices
    assert "Puck" in settings.voices_for("gemini")
    assert {"marin", "cedar"} <= set(settings.voices_for("openai"))


def test_default_voice_per_provider(settings):
    assert settings.get_voice("gemini") == "Puck"
    assert settings.get_voice("openai") == "marin"


def test_set_voice_is_per_provider(settings, tmp_path):
    # selections for each provider are independent and both persist
    assert settings.set_voice("Kore", provider="gemini")
    assert settings.set_voice("cedar", provider="openai")
    assert settings.get_voice("gemini") == "Kore"
    assert settings.get_voice("openai") == "cedar"

    # active-provider default resolution follows the pin (no persisted choice)
    assert _pinned(tmp_path, "openai").get_voice() == "cedar"
    assert _pinned(tmp_path, "gemini").get_voice() == "Kore"


def test_set_voice_rejects_unknown(settings):
    assert settings.set_voice("alloy", provider="gemini") is False  # openai voice
    assert settings.set_voice("Puck", provider="openai") is False  # gemini voice
    assert settings.set_voice("nope", provider="gemini") is False


def test_mcp_servers_hide_env_values_and_roundtrip(settings):
    server = settings.upsert_mcp_server(
        {
            "name": "github",
            "command": "npx",
            "args": "-y @modelcontextprotocol/server-github",
            "env": "GITHUB_PERSONAL_ACCESS_TOKEN=secret\n# ignored\nNO_EQUALS",
            "allowed_tools": "search,read",
            "blocked_tools": ["write"],
        }
    )

    assert server["name"] == "github"
    assert server["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert server["allowed_tools"] == ["search", "read"]
    assert server["blocked_tools"] == ["write"]
    assert server["env_keys"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert "env" not in server

    public = settings.list_mcp_servers()
    assert public == [server]
    assert "env" not in public[0]

    private = settings.list_mcp_servers(include_env=True)
    assert private[0]["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": "secret"}


def test_mcp_servers_roundtrip(settings):
    settings.upsert_mcp_server({"name": "github", "command": "uvx", "args": ["mcp-server-github"]})
    assert len(settings.list_mcp_servers()) == 1
    assert settings.list_mcp_servers()[0]["command"] == "uvx"

    assert settings.delete_mcp_server("github") is True
    assert settings.delete_mcp_server("github") is False


def test_two_profiles_settings_are_isolated(tmp_path):
    # Two profiles, two settings files — no cross-talk.
    a = Settings(tmp_path / "a" / "config.yaml")
    b = Settings(tmp_path / "b" / "config.yaml")
    a.set_voice("Kore", provider="gemini")
    a.upsert_mcp_server({"name": "only-a", "command": "npx"})
    assert a.get_voice("gemini") == "Kore"
    assert b.get_voice("gemini") == "Puck"  # untouched → default
    assert [s["name"] for s in b.list_mcp_servers()] == []


def test_focuses_roundtrip_and_normalisation(settings):
    # Fresh install: no focuses until chosen.
    assert settings.get_focuses() == []
    # Client sends lowercase slugs; order preserved, returned as stored.
    assert settings.set_focuses(["research", "coding"]) == ["research", "coding"]
    assert settings.get_focuses() == ["research", "coding"]
    # Normalises case, trims, dedups; drops junk (spaces / long strings).
    stored = settings.set_focuses(["Writing", " data ", "writing", "not a slug!", "coding"])
    assert stored == ["writing", "data", "coding"]
    assert settings.get_focuses() == ["writing", "data", "coding"]
    # A comma-string is accepted too.
    assert settings.set_focuses("images, research") == ["images", "research"]
    # Clearing resets to [].
    assert settings.set_focuses([]) == []
    assert settings.get_focuses() == []


def test_focuses_are_per_profile(tmp_path):
    a = Settings(tmp_path / "a" / "config.yaml")
    b = Settings(tmp_path / "b" / "config.yaml")
    a.set_focuses(["research"])
    assert a.get_focuses() == ["research"]
    assert b.get_focuses() == []  # untouched → default


def test_reply_timeout_roundtrips(settings):
    assert settings.set_reply_timeout(480) == 480.0
    assert settings._read()["gateway"]["reply_timeout_s"] == 480.0
    with pytest.raises(ValueError, match="greater than zero"):
        settings.set_reply_timeout(0)
