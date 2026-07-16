"""Secret entity store: named reusable API keys (unique by value), the per-provider
Default designation, and env sync. See CONTEXT.md "Secrets" and ADR 0005."""

import json
import os

import pytest

from assistant import secrets
from assistant.config import data_dir


def _raw():
    return json.loads((data_dir() / "secrets.json").read_text())


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    """Secret default-sync mutates os.environ directly (not via monkeypatch) — start
    each test with the provider slots empty and let monkeypatch restore after."""
    for env in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)


def test_create_list_and_hint_never_raw():
    v = secrets.create_secret("Work OpenAI", "sk-test-1234", provider="openai")
    assert v["id"].startswith("s_")
    assert v["hint"] == "…1234"
    assert "value" not in v
    assert secrets.list_secrets() == [v]
    assert "sk-test-1234" not in json.dumps(secrets.list_secrets())
    assert secrets.secret_value(v["id"]) == "sk-test-1234"
    assert secrets.get_secret(v["id"]) == v
    assert secrets.get_secret("s_missing") is None
    assert secrets.secret_value("s_missing") == ""


def test_create_validation():
    with pytest.raises(ValueError):
        secrets.create_secret("", "sk-x")
    with pytest.raises(ValueError):
        secrets.create_secret("X", "")
    with pytest.raises(ValueError):
        secrets.create_secret("X", "sk-x", provider="bogus")
    with pytest.raises(ValueError):
        secrets.create_secret("X", "sk-x", default=True)  # default needs a provider tag


def test_unique_by_value():
    a = secrets.create_secret("A", "sk-same")
    with pytest.raises(secrets.DuplicateValue) as exc:
        secrets.create_secret("B", "sk-same")
    assert exc.value.existing["id"] == a["id"]
    b = secrets.create_secret("B", "sk-other")
    with pytest.raises(secrets.DuplicateValue):
        secrets.update_secret(b["id"], value="sk-same")
    secrets.update_secret(a["id"], value="sk-same")  # own current value is fine


def test_find_by_value():
    a = secrets.create_secret("A", "sk-find")
    assert secrets.find_secret_by_value("sk-find")["id"] == a["id"]
    assert secrets.find_secret_by_value("nope") is None
    assert secrets.find_secret_by_value("") is None


def test_default_displaces_and_syncs_env():
    a = secrets.create_secret("A", "sk-1", provider="openai", default=True)
    assert os.environ["OPENAI_API_KEY"] == "sk-1"
    b = secrets.create_secret("B", "sk-2", provider="openai", default=True)
    assert secrets.default_secret("openai")["id"] == b["id"]
    assert secrets.get_secret(a["id"])["default"] is False
    assert os.environ["OPENAI_API_KEY"] == "sk-2"
    assert secrets.default_secret("gemini") is None


def test_set_default_and_untagging_drops_default():
    a = secrets.create_secret("A", "sk-1", provider="openai")
    assert secrets.set_default(a["id"]) is True
    assert os.environ["OPENAI_API_KEY"] == "sk-1"
    untagged = secrets.create_secret("U", "sk-2")
    assert secrets.set_default(untagged["id"]) is False  # untagged can't be default
    assert secrets.set_default("s_missing") is False
    secrets.update_secret(a["id"], provider="")  # untagging a Default drops it
    assert secrets.default_secret("openai") is None
    assert "OPENAI_API_KEY" not in os.environ


def test_rotate_default_value_updates_env():
    a = secrets.create_secret("A", "sk-1", provider="gemini", default=True)
    secrets.update_secret(a["id"], value="sk-9")
    assert os.environ["GEMINI_API_KEY"] == "sk-9"


def test_update_unknown_and_validation():
    with pytest.raises(KeyError):
        secrets.update_secret("s_missing", name="X")
    a = secrets.create_secret("A", "sk-1")
    with pytest.raises(ValueError):
        secrets.update_secret(a["id"], name="")
    with pytest.raises(ValueError):
        secrets.update_secret(a["id"], value="")
    with pytest.raises(ValueError):
        secrets.update_secret(a["id"], default=True)  # still untagged


def test_delete_always_allowed_default_pops_env():
    a = secrets.create_secret("A", "sk-1", provider="openai", default=True)
    assert secrets.delete_secret(a["id"]) is True
    assert "OPENAI_API_KEY" not in os.environ
    assert secrets.list_secrets() == []
    assert secrets.delete_secret("s_missing") is False


def test_file_is_0600():
    secrets.create_secret("A", "sk-1")
    mode = (data_dir() / "secrets.json").stat().st_mode & 0o777
    assert mode == 0o600
    assert _raw()["secrets"][0]["value"] == "sk-1"  # raw on disk (like a .env), 0600


def test_set_key_upserts_default_secret():
    assert secrets.set_key("openai", "sk-on-1111") is True
    d = secrets.default_secret("openai")
    assert d["name"] == "OpenAI" and d["hint"] == "…1111" and d["default"] is True
    assert os.environ["OPENAI_API_KEY"] == "sk-on-1111"
    secrets.set_key("openai", "sk-on-2222")  # same provider → update, not a 2nd entry
    assert len(secrets.list_secrets()) == 1
    assert secrets.default_secret("openai")["hint"] == "…2222"


def test_set_key_snaps_to_existing_value():
    a = secrets.create_secret("Mine", "sk-dup")
    secrets.set_key("openai", "sk-dup")
    assert len(secrets.list_secrets()) == 1
    d = secrets.default_secret("openai")
    assert d["id"] == a["id"] and d["name"] == "Mine"  # adopted, name kept


def test_set_key_clear_deletes_default():
    secrets.set_key("anthropic", "sk-a-1")
    assert secrets.set_key("anthropic", "") is True
    assert secrets.default_secret("anthropic") is None
    assert secrets.list_secrets() == []
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_set_key_github_ollama_and_unknown(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert secrets.set_key("github", "ghp_x") is True
    assert _raw()["github"] == "ghp_x"
    assert os.environ["GITHUB_TOKEN"] == "ghp_x"
    assert secrets.set_key("github", "") is True
    assert secrets.set_key("ollama", "http://box:11434") is True
    assert _raw()["ollama_base_url"] == "http://box:11434"
    assert secrets.set_key("bogus", "x") is False


def test_status_prefers_default_secret_then_env(monkeypatch):
    assert secrets.status()["openai"]["set"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-5678")
    assert secrets.status()["openai"] == {"set": True, "hint": "…5678"}
    secrets.create_secret("A", "sk-def-4321", provider="openai", default=True)
    assert secrets.status()["openai"]["hint"] == "…4321"


def test_load_into_env_defaults_and_env_only_survives(monkeypatch):
    secrets.create_secret("A", "sk-def-1", provider="gemini", default=True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-only")
    secrets.load_into_env()
    assert os.environ["GEMINI_API_KEY"] == "sk-def-1"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-env-only"  # no Default → untouched


def test_migrate_legacy_provider_and_config_keys():
    from assistant import live_configs, llm_configs

    c1 = llm_configs.save_config({"name": "GPT", "type": "openai", "model": "gpt-4o"})
    c2 = llm_configs.save_config({"name": "Mini", "type": "openai", "model": "o4-mini"})
    lv = live_configs.save_config({"name": "Voice", "provider": "openai"})
    p = data_dir() / "secrets.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "openai": "sk-shared",
                "llm_keys": {c1["id"]: "sk-shared", c2["id"]: "sk-own", "c_orphan": "sk-dead"},
                "live_keys": {lv["id"]: "sk-own"},
                "channels": {"TELEGRAM_BOT_TOKEN": "tg"},
                "github": "ghp_keep",
            }
        )
    )
    secrets.migrate()
    data = _raw()
    assert "llm_keys" not in data and "live_keys" not in data and "openai" not in data
    assert data["channels"] == {"TELEGRAM_BOT_TOKEN": "tg"}
    assert data["github"] == "ghp_keep"
    views = secrets.list_secrets()
    # sk-shared deduped into ONE secret (provider survivor: tag + Default + name);
    # sk-own shared by an llm and a live config; the orphan key is dropped.
    assert len(views) == 2
    shared = secrets.default_secret("openai")
    assert shared["name"] == "OpenAI" and shared["hint"] == "…ared"
    own = next(v for v in views if v["id"] != shared["id"])
    assert own["name"] == "Mini key" and own["provider"] == ""
    assert llm_configs.get_config(c1["id"])["secret_id"] == shared["id"]
    assert llm_configs.get_config(c2["id"])["secret_id"] == own["id"]
    assert live_configs.get_config(lv["id"])["secret_id"] == own["id"]
    secrets.migrate()  # idempotent
    assert len(secrets.list_secrets()) == 2


def test_migrate_fresh_install_is_noop_marker():
    secrets.migrate()
    assert _raw()["secrets"] == []
    a = secrets.create_secret("A", "sk-1")
    secrets.migrate()  # marker present → never touches existing Secrets
    assert secrets.list_secrets() == [a]
