"""Provider-aware realtime voice settings + provider registry.

Settings are per-profile: each test builds a `Settings(path)` bound to a tmp file,
so there is no shared global settings state.
"""

import pytest

from assistant import voice_providers
from assistant.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "settings.json")


def test_voice_provider_env(settings, monkeypatch):
    monkeypatch.delenv("AG2ASSISTANT_VOICE_PROVIDER", raising=False)
    assert settings.voice_provider() == "gemini"  # default
    monkeypatch.setenv("AG2ASSISTANT_VOICE_PROVIDER", "openai")
    assert settings.voice_provider() == "openai"
    monkeypatch.setenv("AG2ASSISTANT_VOICE_PROVIDER", "OpenAI")  # case-insensitive
    assert settings.voice_provider() == "openai"
    monkeypatch.setenv("AG2ASSISTANT_VOICE_PROVIDER", "bogus")  # unknown → gemini
    assert settings.voice_provider() == "gemini"


def test_persisted_voice_provider_wins_over_env(settings, monkeypatch):
    # A profile's persisted choice takes precedence over the env fallback.
    monkeypatch.setenv("AG2ASSISTANT_VOICE_PROVIDER", "gemini")
    assert settings.set_voice_provider("openai") is True
    assert settings.voice_provider() == "openai"
    assert settings.set_voice_provider("bogus") is False


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


def test_set_voice_is_per_provider(settings, monkeypatch):
    # selections for each provider are independent and both persist
    assert settings.set_voice("Kore", provider="gemini")
    assert settings.set_voice("cedar", provider="openai")
    assert settings.get_voice("gemini") == "Kore"
    assert settings.get_voice("openai") == "cedar"

    # active-provider default resolution follows the env var (no persisted choice)
    monkeypatch.setenv("AG2ASSISTANT_VOICE_PROVIDER", "openai")
    assert settings.get_voice() == "cedar"
    monkeypatch.setenv("AG2ASSISTANT_VOICE_PROVIDER", "gemini")
    assert settings.get_voice() == "Kore"


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
    a = Settings(tmp_path / "a" / "settings.json")
    b = Settings(tmp_path / "b" / "settings.json")
    a.set_voice("Kore", provider="gemini")
    a.upsert_mcp_server({"name": "only-a", "command": "npx"})
    assert a.get_voice("gemini") == "Kore"
    assert b.get_voice("gemini") == "Puck"  # untouched → default
    assert [s["name"] for s in b.list_mcp_servers()] == []


def test_project_folder_roundtrips(settings):
    # Fresh install: no project folder until chosen.
    assert settings.get_project_folder() == ""
    settings.set_project_folder("/tmp/my-project")
    assert settings.get_project_folder() == "/tmp/my-project"
    # Clearing it (empty/None) resets to "".
    settings.set_project_folder("")
    assert settings.get_project_folder() == ""
