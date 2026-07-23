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


# ---- per-profile Live Active override (ADR 0015) ------------------------------
# Symmetric with the Text override: env pin > profile override > install-wide Active
# > env fallback, resolved at voice.profile_live_config. A dangling override degrades
# to the install-wide Active; one profile's override never affects another's.


def _settings(tmp_path, pid):
    from assistant.settings import Settings

    return Settings(tmp_path / pid / "config.yaml")


def test_live_override_absent_inherits_install_active(tmp_path):
    from assistant import voice

    x = live_configs.save_config({"name": "X", "provider": "gemini"})
    live_configs.set_active(x["id"])
    assert voice.profile_live_config(_settings(tmp_path, "work"))["id"] == x["id"]


def test_live_override_wins_and_isolated(tmp_path):
    from assistant import voice

    x = live_configs.save_config({"name": "X", "provider": "gemini"})
    y = live_configs.save_config({"name": "Y", "provider": "openai"})
    live_configs.set_active(x["id"])
    work = _settings(tmp_path, "work")
    home = _settings(tmp_path, "home")
    work.set_live_override(y["id"])
    assert voice.profile_live_config(work)["id"] == y["id"]  # override wins
    assert voice.profile_live_config(home)["id"] == x["id"]  # home inherits
    assert live_configs.active_id() == x["id"]  # install-wide Active never moved
    # Clearing restores inheritance.
    work.set_live_override("")
    assert voice.profile_live_config(work)["id"] == x["id"]


def test_live_dangling_override_degrades_silently(tmp_path):
    from assistant import voice

    x = live_configs.save_config({"name": "X", "provider": "gemini"})
    live_configs.set_active(x["id"])
    s = _settings(tmp_path, "work")
    s.set_live_override("lv_deleted_ghost")
    assert voice.profile_live_config(s)["id"] == x["id"]  # no error; falls back
