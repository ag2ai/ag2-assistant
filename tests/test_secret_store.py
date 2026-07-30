"""Secret entity store: named reusable API keys (unique by value), the per-provider
Default designation, and env sync. See CONTEXT.md "Secrets" and ADR 0005."""

import json

import pytest

from assistant.live_configs import LiveConfigStore
from assistant.llm_configs import LlmConfigStore
from assistant.secrets import DuplicateValue, SecretStore


@pytest.fixture
def store(paths) -> SecretStore:
    """The secrets store over an isolated layout."""
    return SecretStore(paths)


@pytest.fixture
def llm_store(paths) -> LlmConfigStore:
    """The named-LLM-configuration store over that same layout."""
    return LlmConfigStore(paths)


@pytest.fixture
def live_store(paths) -> LiveConfigStore:
    """The Live (voice) configuration store over that same layout."""
    return LiveConfigStore(paths)


def _raw(paths):
    return json.loads(paths.secrets_json.read_text())


def test_create_list_and_hint_never_raw(store, paths):
    v = store.create_secret("Work OpenAI", "sk-test-1234", provider="openai")
    assert v["id"].startswith("s_")
    assert v["hint"] == "…1234"
    assert "value" not in v
    assert store.list_secrets() == [v]
    assert "sk-test-1234" not in json.dumps(store.list_secrets())
    assert store.secret_value(v["id"]) == "sk-test-1234"
    assert store.get_secret(v["id"]) == v
    assert store.get_secret("s_missing") is None
    assert store.secret_value("s_missing") == ""


def test_create_validation(store):
    with pytest.raises(ValueError):
        store.create_secret("", "sk-x")
    with pytest.raises(ValueError):
        store.create_secret("X", "")
    with pytest.raises(ValueError):
        store.create_secret("X", "sk-x", provider="bogus")
    with pytest.raises(ValueError):
        store.create_secret("X", "sk-x", default=True)  # default needs a provider tag


def test_unique_by_value(store):
    a = store.create_secret("A", "sk-same")
    with pytest.raises(DuplicateValue) as exc:
        store.create_secret("B", "sk-same")
    assert exc.value.existing["id"] == a["id"]
    b = store.create_secret("B", "sk-other")
    with pytest.raises(DuplicateValue):
        store.update_secret(b["id"], value="sk-same")
    store.update_secret(a["id"], value="sk-same")  # own current value is fine


def test_find_by_value(store):
    a = store.create_secret("A", "sk-find")
    assert store.find_secret_by_value("sk-find")["id"] == a["id"]
    assert store.find_secret_by_value("nope") is None
    assert store.find_secret_by_value("") is None


def test_default_displaces_and_syncs_env(store):
    a = store.create_secret("A", "sk-1", provider="openai", default=True)
    assert store.env_overlay()["OPENAI_API_KEY"] == "sk-1"
    b = store.create_secret("B", "sk-2", provider="openai", default=True)
    assert store.default_secret("openai")["id"] == b["id"]
    assert store.get_secret(a["id"])["default"] is False
    assert store.env_overlay()["OPENAI_API_KEY"] == "sk-2"
    assert store.default_secret("gemini") is None


def test_set_default_and_untagging_drops_default(store):
    a = store.create_secret("A", "sk-1", provider="openai")
    assert store.set_default(a["id"]) is True
    assert store.env_overlay()["OPENAI_API_KEY"] == "sk-1"
    untagged = store.create_secret("U", "sk-2")
    assert store.set_default(untagged["id"]) is False  # untagged can't be default
    assert store.set_default("s_missing") is False
    store.update_secret(a["id"], provider="")  # untagging a Default drops it
    assert store.default_secret("openai") is None
    assert "OPENAI_API_KEY" not in store.env_overlay()


def test_rotate_default_value_updates_env(store):
    a = store.create_secret("A", "sk-1", provider="gemini", default=True)
    store.update_secret(a["id"], value="sk-9")
    assert store.env_overlay()["GEMINI_API_KEY"] == "sk-9"


def test_update_unknown_and_validation(store):
    with pytest.raises(KeyError):
        store.update_secret("s_missing", name="X")
    a = store.create_secret("A", "sk-1")
    with pytest.raises(ValueError):
        store.update_secret(a["id"], name="")
    with pytest.raises(ValueError):
        store.update_secret(a["id"], value="")
    with pytest.raises(ValueError):
        store.update_secret(a["id"], default=True)  # still untagged


def test_delete_always_allowed_default_pops_env(store):
    a = store.create_secret("A", "sk-1", provider="openai", default=True)
    assert store.delete_secret(a["id"]) is True
    assert "OPENAI_API_KEY" not in store.env_overlay()
    assert store.list_secrets() == []
    assert store.delete_secret("s_missing") is False


def test_file_is_0600(paths, store):
    store.create_secret("A", "sk-1")
    mode = paths.secrets_json.stat().st_mode & 0o777
    assert mode == 0o600
    assert _raw(paths)["secrets"][0]["value"] == "sk-1"  # raw on disk (like a .env), 0600


def test_set_key_upserts_default_secret(store):
    assert store.set_key("openai", "sk-on-1111") is True
    d = store.default_secret("openai")
    assert d["name"] == "OpenAI" and d["hint"] == "…1111" and d["default"] is True
    assert store.env_overlay()["OPENAI_API_KEY"] == "sk-on-1111"
    store.set_key("openai", "sk-on-2222")  # same provider → update, not a 2nd entry
    assert len(store.list_secrets()) == 1
    assert store.default_secret("openai")["hint"] == "…2222"


def test_set_key_snaps_to_existing_value(store):
    a = store.create_secret("Mine", "sk-dup")
    store.set_key("openai", "sk-dup")
    assert len(store.list_secrets()) == 1
    d = store.default_secret("openai")
    assert d["id"] == a["id"] and d["name"] == "Mine"  # adopted, name kept


def test_set_key_clear_deletes_default(store):
    store.set_key("anthropic", "sk-a-1")
    assert store.set_key("anthropic", "") is True
    assert store.default_secret("anthropic") is None
    assert store.list_secrets() == []
    assert "ANTHROPIC_API_KEY" not in store.env_overlay()


def test_set_key_github_ollama_and_unknown(paths, store):
    assert store.set_key("github", "ghp_x") is True
    assert _raw(paths)["github"] == "ghp_x"
    assert store.env_overlay()["GITHUB_TOKEN"] == "ghp_x"
    assert store.set_key("github", "") is True
    assert store.set_key("ollama", "http://box:11434") is True
    assert _raw(paths)["ollama_base_url"] == "http://box:11434"
    assert store.set_key("bogus", "x") is False


def test_status_prefers_default_secret_then_env(store):
    """status answers about the env it is handed; a Default Secret wins over it."""
    assert store.status({})["openai"]["set"] is False
    ambient = {"OPENAI_API_KEY": "sk-env-5678"}
    assert store.status(store.merged_env(ambient))["openai"] == {"set": True, "hint": "…5678"}
    store.create_secret("A", "sk-def-4321", provider="openai", default=True)
    assert store.status(store.merged_env(ambient))["openai"]["hint"] == "…4321"


def test_merged_env_layers_defaults_over_an_env_only_key(store):
    store.create_secret("A", "sk-def-1", provider="gemini", default=True)
    merged = store.merged_env({"ANTHROPIC_API_KEY": "sk-env-only"})
    assert merged["GEMINI_API_KEY"] == "sk-def-1"
    assert merged["ANTHROPIC_API_KEY"] == "sk-env-only"  # no Default → survives


def test_migrate_legacy_provider_and_config_keys(paths, live_store, llm_store, store):

    c1 = llm_store.save_config({"name": "GPT", "type": "openai", "model": "gpt-4o"})
    c2 = llm_store.save_config({"name": "Mini", "type": "openai", "model": "o4-mini"})
    lv = live_store.save_config({"name": "Voice", "provider": "openai"})
    p = paths.secrets_json
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
    store.migrate()
    data = _raw(paths)
    assert "llm_keys" not in data and "live_keys" not in data and "openai" not in data
    assert data["channels"] == {"TELEGRAM_BOT_TOKEN": "tg"}
    assert data["github"] == "ghp_keep"
    views = store.list_secrets()
    # sk-shared deduped into ONE secret (provider survivor: tag + Default + name);
    # sk-own shared by an llm and a live config; the orphan key is dropped.
    assert len(views) == 2
    shared = store.default_secret("openai")
    assert shared["name"] == "OpenAI" and shared["hint"] == "…ared"
    own = next(v for v in views if v["id"] != shared["id"])
    assert own["name"] == "Mini key" and own["provider"] == ""
    assert llm_store.get_config(c1["id"])["secret_id"] == shared["id"]
    assert llm_store.get_config(c2["id"])["secret_id"] == own["id"]
    assert live_store.get_config(lv["id"])["secret_id"] == own["id"]
    store.migrate()  # idempotent
    assert len(store.list_secrets()) == 2


def test_migrate_fresh_install_is_noop_marker(paths, store):
    store.migrate()
    assert _raw(paths)["secrets"] == []
    a = store.create_secret("A", "sk-1")
    store.migrate()  # marker present → never touches existing Secrets
    assert store.list_secrets() == [a]
