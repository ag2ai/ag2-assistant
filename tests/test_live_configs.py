"""live_configs key resolution through the Secret store (this module previously had
no test coverage — added with the Secret entity)."""

import os

from assistant import live_configs, secrets


def test_resolve_key_prefers_secret_then_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    e = live_configs.save_config({"name": "V", "provider": "openai"})
    assert e["secret_id"] == ""
    assert live_configs.resolve_key(e) == ""
    assert live_configs.key_source(e) == "none"
    assert live_configs.usable(e) is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert live_configs.resolve_key(e) == "sk-env"
    assert live_configs.key_source(e) == "shared"
    s = secrets.create_secret("K", "sk-live")
    assert live_configs.set_secret_id(e["id"], s["id"]) is True
    e = live_configs.get_config(e["id"])
    assert live_configs.resolve_key(e) == "sk-live"
    assert live_configs.key_source(e) == "secret"
    assert "sk-live" not in os.environ.get("OPENAI_API_KEY", "")


def test_deleted_secret_degrades_to_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-env")
    s = secrets.create_secret("K", "sk-live-2")
    e = live_configs.save_config({"name": "V2", "provider": "gemini", "secret_id": s["id"]})
    assert e["secret_id"] == s["id"]
    secrets.delete_secret(s["id"])
    assert live_configs.resolve_key(e) == "sk-env"
    assert live_configs.key_source(e) == "shared"


def test_set_secret_id_unknown_config():
    assert live_configs.set_secret_id("lv_missing", "s_x") is False
