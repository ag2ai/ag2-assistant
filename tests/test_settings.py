"""Provider-aware realtime voice settings + provider registry."""

import json

from assistant import settings, voice_providers


def test_voice_provider_env(monkeypatch):
    monkeypatch.delenv("AGCLAW_VOICE_PROVIDER", raising=False)
    assert settings.voice_provider() == "gemini"  # default
    monkeypatch.setenv("AGCLAW_VOICE_PROVIDER", "openai")
    assert settings.voice_provider() == "openai"
    monkeypatch.setenv("AGCLAW_VOICE_PROVIDER", "OpenAI")  # case-insensitive
    assert settings.voice_provider() == "openai"
    monkeypatch.setenv("AGCLAW_VOICE_PROVIDER", "bogus")   # unknown → gemini
    assert settings.voice_provider() == "gemini"


def test_registry_has_both_providers():
    assert set(voice_providers.names()) == {"gemini", "openai"}
    assert voice_providers.get("gemini").default_voice == "Puck"
    assert voice_providers.get("openai").default_voice == "marin"
    assert voice_providers.get("bogus").name == "gemini"  # unknown → default


def test_voices_for_each_provider():
    assert settings.voices_for("gemini") is voice_providers.get("gemini").voices
    assert settings.voices_for("openai") is voice_providers.get("openai").voices
    assert "Puck" in settings.voices_for("gemini")
    assert {"marin", "cedar"} <= set(settings.voices_for("openai"))


def test_default_voice_per_provider():
    assert settings.get_voice("gemini") == "Puck"
    assert settings.get_voice("openai") == "marin"


def test_set_voice_is_per_provider(monkeypatch):
    # selections for each provider are independent and both persist
    assert settings.set_voice("Kore", provider="gemini")
    assert settings.set_voice("cedar", provider="openai")
    assert settings.get_voice("gemini") == "Kore"
    assert settings.get_voice("openai") == "cedar"

    # active-provider default resolution follows the env var
    monkeypatch.setenv("AGCLAW_VOICE_PROVIDER", "openai")
    assert settings.get_voice() == "cedar"
    monkeypatch.setenv("AGCLAW_VOICE_PROVIDER", "gemini")
    assert settings.get_voice() == "Kore"


def test_set_voice_rejects_unknown():
    assert settings.set_voice("alloy", provider="gemini") is False   # openai voice
    assert settings.set_voice("Puck", provider="openai") is False    # gemini voice
    assert settings.set_voice("nope", provider="gemini") is False


def test_legacy_flat_voice_migrates(monkeypatch):
    # an old settings.json stored the voice as a bare string (gemini only)
    p = settings._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"voice": "Sulafat"}))
    assert settings.get_voice("gemini") == "Sulafat"        # read as the gemini value
    assert settings.get_voice("openai") == "marin"          # unaffected → default
    # writing an openai voice upgrades the shape without losing gemini's
    assert settings.set_voice("alloy", provider="openai")
    data = json.loads(p.read_text())
    assert data["voice"] == {"gemini": "Sulafat", "openai": "alloy"}
