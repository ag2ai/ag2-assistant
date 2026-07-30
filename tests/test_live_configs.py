"""live_configs key resolution through the Secret store (this module previously had
no test coverage — added with the Secret entity)."""

from assistant.config import Config
from assistant.live_configs import LiveConfigStore
from assistant.secrets import SecretStore


def test_resolve_key_prefers_secret_then_env(paths):
    store = LiveConfigStore(paths)
    e = store.save_config({"name": "V", "provider": "openai"})
    assert e["secret_id"] == ""
    assert store.resolve_key(e, {}) == ""
    assert store.key_source(e, {}) == "none"
    assert store.usable(e, {}) is False

    shared = {"OPENAI_API_KEY": "sk-env"}
    assert store.resolve_key(e, shared) == "sk-env"
    assert store.key_source(e, shared) == "shared"

    s = SecretStore(paths).create_secret("K", "sk-live")
    assert store.set_secret_id(e["id"], s["id"]) is True
    e = store.get_config(e["id"])
    assert store.resolve_key(e, shared) == "sk-live"
    assert store.key_source(e, shared) == "secret"
    # A non-default Secret is never part of the env overlay — it reaches the call
    # only through the config that references it.
    assert "OPENAI_API_KEY" not in SecretStore(paths).env_overlay()


def test_deleted_secret_degrades_to_fallback(paths):
    store = LiveConfigStore(paths)
    secrets = SecretStore(paths)
    shared = {"GEMINI_API_KEY": "sk-env"}
    s = secrets.create_secret("K", "sk-live-2")
    e = store.save_config({"name": "V2", "provider": "gemini", "secret_id": s["id"]})
    assert e["secret_id"] == s["id"]
    secrets.delete_secret(s["id"])
    assert store.resolve_key(e, shared) == "sk-env"
    assert store.key_source(e, shared) == "shared"


def test_set_secret_id_unknown_config(paths):
    assert LiveConfigStore(paths).set_secret_id("lv_missing", "s_x") is False


# ---- per-profile Live Active override (ADR 0015) ------------------------------
# Symmetric with the Text override: env pin > profile override > install-wide Active
# > env fallback, resolved at voice.profile_live_config. A dangling override degrades
# to the install-wide Active; one profile's override never affects another's.


def _settings(tmp_path, pid):
    from assistant.settings import Settings

    return Settings(tmp_path / pid / "config.yaml")


def test_live_override_absent_inherits_install_active(paths, tmp_path):
    from assistant import voice

    cfg = Config.for_paths(paths)
    x = LiveConfigStore(paths).save_config({"name": "X", "provider": "gemini"})
    LiveConfigStore(paths).set_active(x["id"])
    assert voice.profile_live_config(cfg, _settings(tmp_path, "work"))["id"] == x["id"]


def test_live_override_wins_and_isolated(paths, tmp_path):
    from assistant import voice

    cfg = Config.for_paths(paths)
    store = LiveConfigStore(paths)
    x = store.save_config({"name": "X", "provider": "gemini"})
    y = store.save_config({"name": "Y", "provider": "openai"})
    store.set_active(x["id"])
    work = _settings(tmp_path, "work")
    home = _settings(tmp_path, "home")
    work.set_live_override(y["id"])
    assert voice.profile_live_config(cfg, work)["id"] == y["id"]  # override wins
    assert voice.profile_live_config(cfg, home)["id"] == x["id"]  # home inherits
    assert store.active_id() == x["id"]  # install-wide Active never moved
    # Clearing restores inheritance.
    work.set_live_override("")
    assert voice.profile_live_config(cfg, work)["id"] == x["id"]


def test_live_dangling_override_degrades_silently(paths, tmp_path):
    from assistant import voice

    cfg = Config.for_paths(paths)
    x = LiveConfigStore(paths).save_config({"name": "X", "provider": "gemini"})
    LiveConfigStore(paths).set_active(x["id"])
    s = _settings(tmp_path, "work")
    s.set_live_override("lv_deleted_ghost")
    assert voice.profile_live_config(cfg, s)["id"] == x["id"]  # no error; falls back
