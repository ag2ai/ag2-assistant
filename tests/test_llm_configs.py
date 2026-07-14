"""The install-wide named LLM configuration store, derivation, and migration.

HOME is isolated by the autouse conftest fixture, so each test gets its own empty
``~/.ag2assistant`` (and thus an empty ``llm_configs.json`` to start).
"""

import json

import pytest

from assistant import llm_configs, secrets

# ---- CRUD + validation --------------------------------------------------------


def test_save_validates_and_mints_id():
    with pytest.raises(ValueError):  # bad type
        llm_configs.save_config({"name": "x", "type": "bogus", "model": "m"})
    with pytest.raises(ValueError):  # empty name
        llm_configs.save_config({"name": "", "type": "gemini", "model": "m"})
    with pytest.raises(ValueError):  # empty model
        llm_configs.save_config({"name": "x", "type": "gemini", "model": ""})
    with pytest.raises(ValueError):  # options not a dict
        llm_configs.save_config({"name": "x", "type": "gemini", "model": "m", "options": [1]})

    e = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gemini-x"})
    assert e["id"].startswith("c_")
    assert llm_configs.get_config(e["id"])["name"] == "G"
    assert llm_configs.list_configs() == [e]


def test_update_by_id_and_unknown_id_raises():
    e = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gemini-x"})
    e2 = llm_configs.save_config(
        {"id": e["id"], "name": "G2", "type": "gemini", "model": "gemini-y"}
    )
    assert e2["id"] == e["id"]
    assert llm_configs.get_config(e["id"])["name"] == "G2"
    assert len(llm_configs.list_configs()) == 1  # updated in place, not appended

    with pytest.raises(KeyError):
        llm_configs.save_config({"id": "c_nope", "name": "x", "type": "gemini", "model": "m"})


def test_delete_refuses_active_and_cleans_up():
    a = llm_configs.save_config({"name": "A", "type": "gemini", "model": "gm"})
    b = llm_configs.save_config({"name": "B", "type": "anthropic", "model": "cl"})
    llm_configs.set_active(a["id"])

    assert llm_configs.delete_config("c_ghost") is False  # unknown id
    with pytest.raises(ValueError):  # active → refused
        llm_configs.delete_config(a["id"])

    llm_configs.set_active(b["id"])  # select another first
    assert llm_configs.delete_config(a["id"]) is True
    assert llm_configs.get_config(a["id"]) is None


def test_set_active_unknown_returns_false():
    assert llm_configs.set_active("c_ghost") is False
    assert llm_configs.active_id() is None
    e = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert llm_configs.set_active(e["id"]) is True
    assert llm_configs.active_config()["id"] == e["id"]


# ---- entry_options: per-type forcing + merge order ----------------------------


def test_entry_options_openai_surfaces_and_key_last():
    # type forces "api" over anything in options; base_url is lifted; key merges last.
    e = llm_configs.save_config(
        {
            "name": "O",
            "type": "openai_responses",
            "model": "gpt",
            "base_url": "http://h/v1",
            "options": {"api": "chat", "temperature": 0.5},
        }
    )
    opts = llm_configs.entry_options(e)
    assert opts["api"] == "responses"  # type wins over the options' "api": "chat"
    assert opts["base_url"] == "http://h/v1"
    assert opts["temperature"] == 0.5
    # No per-config key + custom base_url → a NON-EMPTY placeholder is forced, so the
    # shared provider key is never transmitted to a third-party/local endpoint (and
    # the OpenAI SDK gets the non-empty key it requires).
    assert opts["api_key"] == "unused"

    secrets.set_config_key(e["id"], "sk-xyz-9999")
    assert llm_configs.entry_options(e)["api_key"] == "sk-xyz-9999"  # merged last


def test_entry_options_chat_and_ollama_and_gemini():
    chat = llm_configs.save_config({"name": "C", "type": "openai", "model": "m"})
    assert llm_configs.entry_options(chat) == {"api": "chat"}

    olm = llm_configs.save_config(
        {"name": "L", "type": "ollama", "model": "llama3.2", "host": "http://h:11434"}
    )
    assert llm_configs.entry_options(olm) == {"host": "http://h:11434"}

    gem = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert llm_configs.entry_options(gem) == {}


# ---- apply_active: derivation, env precedence, empty-store no-op ---------------


def test_apply_active_empty_store_is_noop():
    from assistant.config import Config

    cfg = Config()
    cfg.llm.provider = "sentinel"
    llm_configs.apply_active(cfg)  # no store → unchanged
    assert cfg.llm.provider == "sentinel"


def test_apply_active_derives_and_env_still_wins(monkeypatch):
    from assistant.config import load_config

    monkeypatch.delenv("AG2ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AG2ASSISTANT_MODEL", raising=False)
    e = llm_configs.save_config({"name": "A", "type": "anthropic", "model": "claude-x"})
    llm_configs.set_active(e["id"])

    cfg = load_config()
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-x"

    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("AG2ASSISTANT_MODEL", "gemini-z")
    cfg = load_config()
    assert cfg.llm.provider == "gemini"  # env overrides the derived active config
    assert cfg.llm.model == "gemini-z"


def test_apply_active_base_url_suppresses_cheap_aggregate(monkeypatch):
    """An active config pointing at an OpenAI-compatible server (base_url) suppresses
    the cheap-tier aggregate default — its OpenAI model name wouldn't exist there."""
    from assistant.agent import cheap_model
    from assistant.config import load_config

    monkeypatch.delenv("AG2ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AG2ASSISTANT_MODEL", raising=False)
    e = llm_configs.save_config(
        {"name": "Local", "type": "openai", "model": "gemma", "base_url": "http://h:8080/v1"}
    )
    llm_configs.set_active(e["id"])
    cfg = load_config()
    assert cfg.llm.provider == "openai"
    assert cfg.llm.provider_options["openai"]["base_url"] == "http://h:8080/v1"
    assert cheap_model(cfg) is None  # reuse the main model, like Ollama


# ---- usable + image_entry -----------------------------------------------------


def test_usable_by_type_key_and_base_url(monkeypatch):
    olm = llm_configs.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    assert llm_configs.usable(olm) is True  # local, always

    compat = llm_configs.save_config(
        {"name": "B", "type": "openai", "model": "m", "base_url": "http://h/v1"}
    )
    assert llm_configs.usable(compat) is True  # base_url needs no real key

    gem = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})
    monkeypatch.setattr(
        secrets,
        "status",
        lambda: {"gemini": {"set": False}, "openai": {"set": False}, "anthropic": {"set": False}},
    )
    assert llm_configs.usable(gem) is False  # no env key, no per-config key
    secrets.set_config_key(gem["id"], "k")
    assert llm_configs.usable(gem) is True  # per-config key makes it usable


def test_image_entry_follows_active_only():
    """Images run on the SELECTED configuration or not at all — no fallback hunting
    through the list (switching models must never silently reroute images)."""
    olm = llm_configs.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    compat = llm_configs.save_config(
        {"name": "B", "type": "openai", "model": "m", "base_url": "http://h/v1"}
    )
    gem = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})

    # active can't generate images → images unavailable, even with gemini in the list
    llm_configs.set_active(compat["id"])
    assert llm_configs.image_entry() is None

    # active is image-capable → used directly
    llm_configs.set_active(gem["id"])
    assert llm_configs.image_entry()["id"] == gem["id"]
    assert olm["type"] == "ollama"  # (kept as a non-capable config in the list)
    assert llm_configs.image_capable(gem) and not llm_configs.image_capable(compat)


def test_image_entry_none_when_no_capable():
    llm_configs.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    llm_configs.save_config({"name": "A", "type": "anthropic", "model": "cl"})
    llm_configs.save_config(
        {"name": "B", "type": "openai", "model": "m", "base_url": "http://h/v1"}
    )
    assert llm_configs.image_entry() is None


# ---- per-config secret never leaks --------------------------------------------


def test_config_key_stored_but_not_in_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    e = llm_configs.save_config({"name": "O", "type": "openai", "model": "m"})
    assert secrets.config_key(e["id"]) == ""
    assert secrets.set_config_key(e["id"], "sk-secret-4242") is True
    assert secrets.config_key(e["id"]) == "sk-secret-4242"
    assert secrets.config_key_hint(e["id"]) == {"set": True, "hint": "…4242"}
    # deliberately NOT loaded into os.environ (unlike provider keys)
    import os

    assert os.environ.get("OPENAI_API_KEY") is None
    # status() (per-provider) never exposes per-config keys
    assert "…4242" not in json.dumps(secrets.status())
    assert secrets.set_config_key(e["id"], "") is True  # clear
    assert secrets.config_key(e["id"]) == ""


# ---- one-time migration -------------------------------------------------------


def test_migration_folds_legacy_llm_and_is_idempotent(monkeypatch):
    from assistant import profiles
    from assistant.config import data_dir
    from assistant.gateway.migration import migrate_llm_configs

    monkeypatch.delenv("AG2ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AG2ASSISTANT_MODEL", raising=False)

    # An install with a config.json default + a profile that overrode to a compat server.
    data_dir().mkdir(parents=True, exist_ok=True)
    (data_dir() / "config.json").write_text(
        json.dumps({"llm": {"provider": "gemini", "model": "gemini-3.5-flash"}})
    )
    work = profiles.create_profile("Work", "teal")
    home = profiles.create_profile("Home", "coral")
    profiles.set_active_default(work.id)
    profiles.profile_dir(work.id).mkdir(parents=True, exist_ok=True)
    profiles.profile_dir(home.id).mkdir(parents=True, exist_ok=True)
    (profiles.profile_dir(work.id) / "settings.json").write_text(
        json.dumps(
            {
                "llm": {"provider": "openai", "model": "gemma-4"},
                "llm_options": {"openai": {"base_url": "http://192.168.0.55:8080/v1"}},
                "voice_provider": "gemini",
            }
        )
    )

    # migrate_llm_configs writes the legacy llm_configs.json (migrate_config_files
    # folds it into config.yaml later), so verify that file directly.
    assert migrate_llm_configs() is True
    store = json.loads((data_dir() / "llm_configs.json").read_text())
    configs = store["configs"]
    assert {c["type"] for c in configs} == {"gemini", "openai"}

    work_cfg = next(c for c in configs if c["type"] == "openai")
    assert work_cfg["model"] == "gemma-4"
    assert work_cfg["base_url"] == "http://192.168.0.55:8080/v1"  # lifted to first-class
    assert "base_url" not in work_cfg["options"]

    # active is the active-default profile's (Work) entry
    assert store["active"] == work_cfg["id"]

    # legacy keys stripped from the profile settings; other keys preserved
    ws = json.loads((profiles.profile_dir(work.id) / "settings.json").read_text())
    assert "llm" not in ws and "llm_options" not in ws
    assert ws["voice_provider"] == "gemini"

    # idempotent — a second run is a no-op
    before = json.loads((data_dir() / "llm_configs.json").read_text())
    assert migrate_llm_configs() is False
    assert json.loads((data_dir() / "llm_configs.json").read_text()) == before


def test_migration_noop_on_fresh_install():
    from assistant import profiles
    from assistant.gateway.migration import migrate_llm_configs

    # A profile with settings but no llm block, and no config.json → nothing to migrate.
    p = profiles.create_profile("Solo", "teal")
    profiles.profile_dir(p.id).mkdir(parents=True, exist_ok=True)
    (profiles.profile_dir(p.id) / "settings.json").write_text(
        json.dumps({"voice_provider": "gemini"})
    )

    assert migrate_llm_configs() is False
    assert llm_configs.list_configs() == []
    # store not created → flat defaults apply (neither the legacy json nor a section)
    from assistant.config import data_dir, read_global_config

    assert not (data_dir() / "llm_configs.json").exists()
    assert "llm_configs" not in read_global_config()


def test_store_lives_in_global_config_yaml():
    import yaml

    from assistant.config import default_config_path

    # Seed an unrelated key to prove the store preserves neighbours in the shared file.
    p = default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("agent:\n  name: keep-me\n")

    e = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gemini-x"})
    llm_configs.set_active(e["id"])

    data = yaml.safe_load(p.read_text())
    assert data["agent"]["name"] == "keep-me"  # RMW preserved the neighbour section
    assert data["llm_configs"]["active"] == e["id"]
    assert data["llm_configs"]["configs"][0]["model"] == "gemini-x"
    assert not (p.parent / "llm_configs.json").exists()
    assert llm_configs.active_config()["name"] == "G"


# ---- openai_subscription type (ChatGPT sign-in) -------------------------------


def test_subscription_in_types_and_provider():
    assert "openai_subscription" in llm_configs.TYPES
    assert llm_configs.PROVIDER_OF["openai_subscription"] == "openai"


def test_subscription_clean_entry_strips_endpoint_fields():
    # base_url/host are meaningless for subscription — codex_auth owns the endpoint,
    # so a stale/typo'd value must never survive into the stored entry.
    e = llm_configs.save_config(
        {
            "name": "Sub",
            "type": "openai_subscription",
            "model": "gpt-5.5",
            "base_url": "http://sneaky/v1",
            "host": "http://sneaky",
        }
    )
    assert e["base_url"] == ""
    assert e["host"] == ""


def test_subscription_strips_endpoint_fields_and_options():
    # Subscription entries carry no endpoint fields OR advanced options: base_url and
    # the bearer token come from codex_auth, and the ChatGPT backend rejects every
    # tunable parameter (probed live — "Unsupported parameter"), so _clean_entry
    # strips options rather than persist values that only break calls.
    e = llm_configs.save_config(
        {
            "name": "Sub",
            "type": "openai_subscription",
            "model": "gpt-5.6-luna",
            "base_url": "http://stale/v1",
            "options": {"temperature": 0.3},
        }
    )
    assert e["base_url"] == "" and e["options"] == {}
    assert llm_configs.entry_options(e) == {}


def test_subscription_apply_active_sets_and_resets_auth_mode():
    from assistant.config import Config

    sub = llm_configs.save_config(
        {"name": "Sub", "type": "openai_subscription", "model": "gpt-5.5"}
    )
    other = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})

    cfg = Config()
    llm_configs.set_active(sub["id"])
    llm_configs.apply_active(cfg)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.auth_mode == "subscription"

    # Switching back to a normal config MUST reset auth_mode — else it stays sticky
    # and a plain-OpenAI/Gemini config would wrongly route through the ChatGPT backend.
    llm_configs.set_active(other["id"])
    llm_configs.apply_active(cfg)
    assert cfg.llm.provider == "gemini"
    assert cfg.llm.auth_mode == "api_key"


def test_subscription_usable_and_key_source_track_sign_in(monkeypatch):
    from assistant import codex_auth

    e = llm_configs.save_config({"name": "Sub", "type": "openai_subscription", "model": "gpt-5.5"})
    assert llm_configs.key_source(e) == "subscription"  # never key-based

    monkeypatch.setattr(codex_auth, "is_signed_in", lambda: False)
    assert llm_configs.usable(e) is False  # signed out → not usable
    monkeypatch.setattr(codex_auth, "is_signed_in", lambda: True)
    assert llm_configs.usable(e) is True  # signed in → usable


def test_subscription_usable_never_raises(monkeypatch):
    # A missing/broken codex_auth must read as "not signed in", never propagate into
    # the health/usable path.
    from assistant import codex_auth

    def _boom():
        raise RuntimeError("codex_auth exploded")

    monkeypatch.setattr(codex_auth, "is_signed_in", _boom)
    e = llm_configs.save_config({"name": "Sub", "type": "openai_subscription", "model": "gpt-5.5"})
    assert llm_configs.usable(e) is False


def test_subscription_is_image_capable(monkeypatch):
    from assistant import codex_auth

    monkeypatch.setattr(codex_auth, "is_signed_in", lambda: True)
    sub = llm_configs.save_config(
        {"name": "Sub", "type": "openai_subscription", "model": "gpt-5.6-luna"}
    )
    llm_configs.set_active(sub["id"])
    # The ChatGPT backend runs the native image tool (verified live), so an active
    # subscription config powers image generation like Gemini/OpenAI do.
    assert llm_configs.image_entry()["id"] == sub["id"]


# ---- key_source: which key a call would actually send ---------------------------


def test_key_source_resolution(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # base_url / ollama → not_needed (placeholder is sent, never the shared key)
    local = llm_configs.save_config(
        {"name": "L", "type": "openai", "model": "m", "base_url": "http://h/v1"}
    )
    assert llm_configs.key_source(local) == "not_needed"
    olm = llm_configs.save_config({"name": "O", "type": "ollama", "model": "m"})
    assert llm_configs.key_source(olm) == "not_needed"

    # provider endpoint with no key anywhere → none (unusable, chip warns)
    gem = llm_configs.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert llm_configs.key_source(gem) == "none"

    # shared env key present → shared
    monkeypatch.setenv("GEMINI_API_KEY", "shared-key-1234")
    assert llm_configs.key_source(gem) == "shared"

    # a per-config key overrides everything, base_url included
    secrets.set_config_key(gem["id"], "sk-own-5678")
    assert llm_configs.key_source(gem) == "config"
    secrets.set_config_key(local["id"], "sk-own-9999")
    assert llm_configs.key_source(local) == "config"
