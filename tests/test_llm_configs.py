"""The install-wide named LLM configuration store and derivation.

Every test runs on an isolated layout (the ``paths`` fixture), so the store starts
empty and no developer state can reach it. The ambient environment is always an
explicit dict, and the CLI-login types are probed against real executable adapter
stubs on a real search path. The optional provider libraries are the same: a test
states the situation it means to exercise by passing an ``extras`` map that points at
a really-importable or a really-absent module, so the real ``find_spec`` runs and the
dev venv's installed set never decides the outcome.
"""

import importlib.util
import json

import pytest
import yaml

from assistant.agent import cheap_model
from assistant.config import Config, resolve_config
from assistant.llm_configs import (
    CLI_LOGIN_TYPES,
    PROVIDER_EXTRA,
    PROVIDER_OF,
    TYPES,
    LlmConfigStore,
    _clean_entry,
    _module_present,
    deps_status,
    image_capable,
)
from assistant.secrets import SecretStore
from tests.support.apps import write_codex_session
from tests.support.stubs import write_stub

# ``extras`` maps (type → (module, extra)) standing in for "the provider library is
# installed" / "it isn't": ``json`` is always importable, the other name never is.
PRESENT_EXTRAS = {ctype: ("json", extra) for ctype, (_, extra) in PROVIDER_EXTRA.items()}
ABSENT_EXTRAS = {
    ctype: ("assistant_absent_provider_lib", extra) for ctype, (_, extra) in PROVIDER_EXTRA.items()
}


@pytest.fixture
def store(paths) -> LlmConfigStore:
    return LlmConfigStore(paths)


@pytest.fixture
def secret_store(paths) -> SecretStore:
    return SecretStore(paths)


def _adapters(tmp_path, *names: str) -> list:
    """A search path holding real executable stubs for these ACP adapters."""
    bin_dir = tmp_path / "acp-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        write_stub(bin_dir / name)
    return [bin_dir]


# ---- CRUD + validation --------------------------------------------------------


def test_save_validates_and_mints_id(store):
    with pytest.raises(ValueError):  # bad type
        store.save_config({"name": "x", "type": "bogus", "model": "m"})
    with pytest.raises(ValueError):  # empty name
        store.save_config({"name": "", "type": "gemini", "model": "m"})
    with pytest.raises(ValueError):  # empty model
        store.save_config({"name": "x", "type": "gemini", "model": ""})
    with pytest.raises(ValueError):  # options not a dict
        store.save_config({"name": "x", "type": "gemini", "model": "m", "options": [1]})

    e = store.save_config({"name": "G", "type": "gemini", "model": "gemini-x"})
    assert e["id"].startswith("c_")
    assert store.get_config(e["id"])["name"] == "G"
    assert store.list_configs() == [e]


def test_update_by_id_and_unknown_id_raises(store):
    e = store.save_config({"name": "G", "type": "gemini", "model": "gemini-x"})
    e2 = store.save_config({"id": e["id"], "name": "G2", "type": "gemini", "model": "gemini-y"})
    assert e2["id"] == e["id"]
    assert store.get_config(e["id"])["name"] == "G2"
    assert len(store.list_configs()) == 1  # updated in place, not appended

    with pytest.raises(KeyError):
        store.save_config({"id": "c_nope", "name": "x", "type": "gemini", "model": "m"})


def test_delete_active_moves_active_to_next(store):
    a = store.save_config({"name": "A", "type": "gemini", "model": "gm"})
    b = store.save_config({"name": "B", "type": "anthropic", "model": "cl"})
    store.set_active(a["id"])

    assert store.delete_config("c_ghost") is False  # unknown id

    # Deleting the active config is allowed; active moves to the remaining one.
    assert store.delete_config(a["id"]) is True
    assert store.get_config(a["id"]) is None
    assert store.active_id() == b["id"]

    # Deleting the last remaining (still active) clears active → empty store.
    assert store.delete_config(b["id"]) is True
    assert store.active_id() is None
    assert store.list_configs() == []


def test_set_active_unknown_returns_false(store):
    assert store.set_active("c_ghost") is False
    assert store.active_id() is None
    e = store.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert store.set_active(e["id"]) is True
    assert store.active_config()["id"] == e["id"]


# ---- entry_options: per-type forcing + merge order ----------------------------


def test_entry_options_openai_surfaces_and_key_last(store, secret_store):
    # type forces "api" over anything in options; base_url is lifted; key merges last.
    e = store.save_config(
        {
            "name": "O",
            "type": "openai_responses",
            "model": "gpt",
            "base_url": "http://h/v1",
            "options": {"api": "chat", "temperature": 0.5},
        }
    )
    opts = store.entry_options(e)
    assert opts["api"] == "responses"  # type wins over the options' "api": "chat"
    assert opts["base_url"] == "http://h/v1"
    assert opts["temperature"] == 0.5
    # No per-config key + custom base_url → a NON-EMPTY placeholder is forced, so the
    # shared provider key is never transmitted to a third-party/local endpoint (and
    # the OpenAI SDK gets the non-empty key it requires).
    assert opts["api_key"] == "unused"

    s = secret_store.create_secret("O key", "sk-xyz-9999")
    store.set_secret_id(e["id"], s["id"])
    e = store.get_config(e["id"])  # re-read: entry dicts are copies
    assert store.entry_options(e)["api_key"] == "sk-xyz-9999"  # merged last


def test_entry_options_chat_and_ollama_and_gemini(store):
    chat = store.save_config({"name": "C", "type": "openai", "model": "m"})
    assert store.entry_options(chat) == {"api": "chat"}

    olm = store.save_config(
        {"name": "L", "type": "ollama", "model": "llama3.2", "host": "http://h:11434"}
    )
    assert store.entry_options(olm) == {"host": "http://h:11434"}

    gem = store.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert store.entry_options(gem) == {}


# ---- apply_active: derivation, env precedence, empty-store no-op ---------------


def test_apply_active_empty_store_is_noop(store, paths):
    cfg = Config.for_paths(paths)
    cfg.llm.provider = "sentinel"
    store.apply_active(cfg)  # no store → unchanged
    assert cfg.llm.provider == "sentinel"


def test_apply_active_derives_and_env_still_wins(store, paths):
    e = store.save_config({"name": "A", "type": "anthropic", "model": "claude-x"})
    store.set_active(e["id"])

    cfg = resolve_config({}, paths)
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-x"

    env = {"AG2ASSISTANT_LLM_PROVIDER": "gemini", "AG2ASSISTANT_MODEL": "gemini-z"}
    cfg = resolve_config(env, paths)
    assert cfg.llm.provider == "gemini"  # env overrides the derived active config
    assert cfg.llm.model == "gemini-z"


def test_apply_active_base_url_suppresses_cheap_aggregate(store, paths):
    """An active config pointing at an OpenAI-compatible server (base_url) suppresses
    the cheap-tier aggregate default — its OpenAI model name wouldn't exist there."""
    e = store.save_config(
        {"name": "Local", "type": "openai", "model": "gemma", "base_url": "http://h:8080/v1"}
    )
    store.set_active(e["id"])
    cfg = resolve_config({}, paths)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.provider_options["openai"]["base_url"] == "http://h:8080/v1"
    assert cheap_model(cfg) is None  # reuse the main model, like Ollama


# ---- usable + image_entry -----------------------------------------------------


def test_usable_by_type_key_and_base_url(store, secret_store):
    # This covers the key/base_url logic, so the optional provider libraries are stated
    # present (PRESENT_EXTRAS) instead of depending on what the dev venv has installed.
    olm = store.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    assert store.usable(olm, {}, extras=PRESENT_EXTRAS) is True  # local, no key needed

    compat = store.save_config(
        {"name": "B", "type": "openai", "model": "m", "base_url": "http://h/v1"}
    )
    assert store.usable(compat, {}) is True  # base_url needs no real key

    gem = store.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert store.usable(gem, {}) is False  # no env key, no referenced Secret
    assert store.usable(gem, {"GEMINI_API_KEY": "shared-key-1"}) is True  # shared env key
    s = secret_store.create_secret("G key", "sk-usable-1")
    store.set_secret_id(gem["id"], s["id"])
    gem = store.get_config(gem["id"])
    assert store.usable(gem, {}) is True  # a referenced Secret makes it usable


def test_image_entry_follows_active_only(store):
    """Images run on the SELECTED configuration or not at all — no fallback hunting
    through the list (switching models must never silently reroute images)."""
    olm = store.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    compat = store.save_config(
        {"name": "B", "type": "openai", "model": "m", "base_url": "http://h/v1"}
    )
    gem = store.save_config({"name": "G", "type": "gemini", "model": "gm"})

    # active can't generate images → images unavailable, even with gemini in the list
    store.set_active(compat["id"])
    assert store.image_entry() is None

    # active is image-capable → used directly
    store.set_active(gem["id"])
    assert store.image_entry()["id"] == gem["id"]
    assert olm["type"] == "ollama"  # (kept as a non-capable config in the list)
    assert image_capable(gem) and not image_capable(compat)


def test_image_entry_none_when_no_capable(store):
    store.save_config({"name": "L", "type": "ollama", "model": "llama3.2"})
    store.save_config({"name": "A", "type": "anthropic", "model": "cl"})
    store.save_config({"name": "B", "type": "openai", "model": "m", "base_url": "http://h/v1"})
    assert store.image_entry() is None


# ---- a referenced Secret never leaks -------------------------------------------


def test_secret_reference_flows_to_options_not_env(store, secret_store):
    s = secret_store.create_secret("K", "sk-4242-4242")
    e = store.save_config({"name": "X", "type": "openai", "model": "gpt-4o", "secret_id": s["id"]})
    assert e["secret_id"] == s["id"]
    assert store.entry_options(e)["api_key"] == "sk-4242-4242"
    # A non-default Secret is not part of the store's env overlay — it reaches the
    # call through provider_options only, never as an environment variable.
    assert "OPENAI_API_KEY" not in secret_store.env_overlay()
    assert store.key_source(e, {}) == "secret"
    assert store.usable(e, {}) is True
    # status() (per-provider) never exposes non-default Secrets
    assert "…4242" not in json.dumps(secret_store.status({}))
    # deleting the Secret degrades: dangling reference falls through to env/none
    secret_store.delete_secret(s["id"])
    e = store.get_config(e["id"])
    assert "api_key" not in store.entry_options(e)
    assert store.key_source(e, {}) == "none"


def test_set_secret_id(store, secret_store):
    s = secret_store.create_secret("K2", "sk-k2-1")
    e = store.save_config({"name": "Y", "type": "gemini", "model": "gemini-3.6-flash"})
    assert store.set_secret_id(e["id"], s["id"]) is True
    assert store.get_config(e["id"])["secret_id"] == s["id"]
    assert store.set_secret_id(e["id"], "") is True  # clear
    assert store.get_config(e["id"])["secret_id"] == ""
    assert store.set_secret_id("c_missing", s["id"]) is False


def test_store_lives_in_global_config_yaml(store, paths):
    # Seed an unrelated key to prove the store preserves neighbours in the shared file.
    p = paths.config_yaml
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("agent:\n  name: keep-me\n")

    e = store.save_config({"name": "G", "type": "gemini", "model": "gemini-x"})
    store.set_active(e["id"])

    data = yaml.safe_load(p.read_text())
    assert data["agent"]["name"] == "keep-me"  # RMW preserved the neighbour section
    assert data["llm_configs"]["active"] == e["id"]
    assert data["llm_configs"]["configs"][0]["model"] == "gemini-x"
    assert not (p.parent / "llm_configs.json").exists()
    assert store.active_config()["name"] == "G"


# ---- openai_subscription type (ChatGPT sign-in) -------------------------------


def test_subscription_in_types_and_provider():
    assert "openai_subscription" in TYPES
    assert PROVIDER_OF["openai_subscription"] == "openai"


def test_subscription_clean_entry_strips_endpoint_fields(store):
    # base_url/host are meaningless for subscription — codex_auth owns the endpoint,
    # so a stale/typo'd value must never survive into the stored entry.
    e = store.save_config(
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


def test_subscription_strips_endpoint_fields_and_options(store):
    # Subscription entries carry no endpoint fields OR advanced options: base_url and
    # the bearer token come from codex_auth, and the ChatGPT backend rejects every
    # tunable parameter (probed live — "Unsupported parameter"), so _clean_entry
    # strips options rather than persist values that only break calls.
    e = store.save_config(
        {
            "name": "Sub",
            "type": "openai_subscription",
            "model": "gpt-5.6-luna",
            "base_url": "http://stale/v1",
            "options": {"temperature": 0.3},
        }
    )
    assert e["base_url"] == "" and e["options"] == {}
    assert store.entry_options(e) == {}


def test_subscription_apply_active_sets_and_resets_auth_mode(store, paths):
    sub = store.save_config({"name": "Sub", "type": "openai_subscription", "model": "gpt-5.5"})
    other = store.save_config({"name": "G", "type": "gemini", "model": "gm"})

    cfg = Config.for_paths(paths)
    store.set_active(sub["id"])
    store.apply_active(cfg)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.auth_mode == "subscription"

    # Switching back to a normal config MUST reset auth_mode — else it stays sticky
    # and a plain-OpenAI/Gemini config would wrongly route through the ChatGPT backend.
    store.set_active(other["id"])
    store.apply_active(cfg)
    assert cfg.llm.provider == "gemini"
    assert cfg.llm.auth_mode == "api_key"


def test_subscription_usable_and_key_source_track_sign_in(store, paths):
    e = store.save_config({"name": "Sub", "type": "openai_subscription", "model": "gpt-5.5"})
    assert store.key_source(e, {}) == "subscription"  # never key-based

    assert store.usable(e, {}) is False  # no session on disk → not usable
    write_codex_session(paths)  # a real signed-in session
    assert store.usable(e, {}) is True


def test_subscription_usable_survives_an_unreadable_session(store, paths):
    """A broken token store must read as "not signed in", never propagate into the
    health/usable path."""
    paths.codex_tokens.parent.mkdir(parents=True, exist_ok=True)
    paths.codex_tokens.mkdir()  # a directory where a JSON file belongs
    e = store.save_config({"name": "Sub", "type": "openai_subscription", "model": "gpt-5.5"})
    assert store.usable(e, {}) is False


def test_subscription_is_image_capable(store, paths):
    write_codex_session(paths)
    sub = store.save_config({"name": "Sub", "type": "openai_subscription", "model": "gpt-5.6-luna"})
    store.set_active(sub["id"])
    # The ChatGPT backend runs the native image tool (verified live), so an active
    # subscription config powers image generation like Gemini/OpenAI do.
    assert store.image_entry()["id"] == sub["id"]


# ---- key_source: which key a call would actually send ---------------------------


def test_key_source_resolution(store, secret_store):
    # base_url / ollama → not_needed (placeholder is sent, never the shared key)
    local = store.save_config(
        {"name": "L", "type": "openai", "model": "m", "base_url": "http://h/v1"}
    )
    assert store.key_source(local, {}) == "not_needed"
    olm = store.save_config({"name": "O", "type": "ollama", "model": "m"})
    assert store.key_source(olm, {}) == "not_needed"

    # provider endpoint with no key anywhere → none (unusable, chip warns)
    gem = store.save_config({"name": "G", "type": "gemini", "model": "gm"})
    assert store.key_source(gem, {}) == "none"

    # shared env key present → shared
    assert store.key_source(gem, {"GEMINI_API_KEY": "shared-key-1234"}) == "shared"

    # a referenced Secret overrides everything, base_url included
    s1 = secret_store.create_secret("gem key", "sk-own-5678")
    store.set_secret_id(gem["id"], s1["id"])
    gem = store.get_config(gem["id"])
    assert store.key_source(gem, {"GEMINI_API_KEY": "shared-key-1234"}) == "secret"
    s2 = secret_store.create_secret("local key", "sk-own-9999")
    store.set_secret_id(local["id"], s2["id"])
    local = store.get_config(local["id"])
    assert store.key_source(local, {}) == "secret"


# ---- claude_code (Claude Code CLI login over ACP) -------------------------------


def test_claude_code_type_registered():
    assert "claude_code" in TYPES
    assert PROVIDER_OF["claude_code"] == "claude_code"


def test_claude_code_clean_entry_strips_endpoint_and_secret():
    entry = _clean_entry(
        {
            "type": "claude_code",
            "name": "CC",
            "model": "sonnet",
            "base_url": "http://x",
            "host": "h",
            "secret_id": "s1",
            "options": {"turn_timeout": 60.0},
        }
    )
    # No endpoint/key concepts — auth is the CLI's on-disk login. Options stay:
    # they are ACPConfig constructor overrides, not provider-API kwargs.
    assert entry["base_url"] == "" and entry["host"] == "" and entry["secret_id"] == ""
    assert entry["options"] == {"turn_timeout": 60.0}


def test_claude_code_entry_options_passthrough(store):
    entry = {"type": "claude_code", "options": {"turn_timeout": 60.0}}
    assert store.entry_options(entry) == {"turn_timeout": 60.0}


def test_claude_code_usable_and_key_source(store, tmp_path):
    """Usability follows the adapter really being on the search path."""
    entry = {"type": "claude_code", "model": "sonnet"}
    installed = _adapters(tmp_path, "claude-agent-acp")
    assert store.usable(entry, {}, search_path=installed) is True
    assert store.key_source(entry, {}, search_path=installed) == "cli_login"
    assert store.usable(entry, {}, search_path=[]) is False
    assert store.key_source(entry, {}, search_path=[]) == "none"


def test_cli_login_usable_over_a_host_bridge(store):
    """In bridge mode (Docker) the adapter lives on the host, so a configured bridge
    counts as present even with nothing on the local search path."""
    from assistant.coding.detect import BridgeEndpoint

    entry = {"type": "codex", "model": ""}
    bridge = BridgeEndpoint(host="host.docker.internal", port=8801, token="")
    assert store.usable(entry, {}, search_path=[], bridge=bridge) is True
    assert store.key_source(entry, {}, search_path=[], bridge=bridge) == "cli_login"


def test_a_different_adapter_does_not_make_a_type_usable(store, tmp_path):
    codex_only = _adapters(tmp_path, "codex-acp")
    assert store.usable({"type": "codex", "model": ""}, {}, search_path=codex_only) is True
    assert store.usable({"type": "claude_code", "model": ""}, {}, search_path=codex_only) is False


def test_claude_code_not_image_capable():
    assert image_capable({"type": "claude_code"}) is False


def test_codex_type_registered():
    assert "codex" in TYPES
    assert PROVIDER_OF["codex"] == "codex"


def test_codex_clean_entry_strips_endpoint_and_secret():
    entry = _clean_entry(
        {
            "type": "codex",
            "name": "CX",
            "model": "gpt-5.6-sol[medium]",
            "base_url": "http://x",
            "host": "h",
            "secret_id": "s1",
            "options": {"turn_timeout": 60.0},
        }
    )
    # No endpoint/key concepts — auth is the CLI's on-disk login. Options stay:
    # they are ACPConfig constructor overrides, not provider-API kwargs.
    assert entry["base_url"] == "" and entry["host"] == "" and entry["secret_id"] == ""
    assert entry["options"] == {"turn_timeout": 60.0}


def test_cli_login_clean_entry_allows_empty_model():
    # For the CLI-login types an empty model means "the CLI's own default"
    # (no model env is derived), so it must survive validation for them only.
    for ctype, name in (("codex", "CX"), ("claude_code", "CC")):
        entry = _clean_entry({"type": ctype, "name": name, "model": ""})
        assert entry["model"] == ""


def test_non_cli_login_still_requires_model():
    for ctype in TYPES:
        if ctype in CLI_LOGIN_TYPES:
            continue
        with pytest.raises(ValueError, match="model is required"):
            _clean_entry({"type": ctype, "name": "X", "model": ""})


def test_codex_entry_options_passthrough(store):
    entry = {"type": "codex", "options": {"turn_timeout": 60.0}}
    assert store.entry_options(entry) == {"turn_timeout": 60.0}


def test_codex_usable_and_key_source(store, tmp_path):
    entry = {"type": "codex", "model": ""}
    installed = _adapters(tmp_path, "codex-acp")
    assert store.usable(entry, {}, search_path=installed) is True
    assert store.key_source(entry, {}, search_path=installed) == "cli_login"
    assert store.usable(entry, {}, search_path=[]) is False
    assert store.key_source(entry, {}, search_path=[]) == "none"


def test_codex_not_image_capable():
    assert image_capable({"type": "codex"}) is False


# ---- optional provider libraries ----------------------------------------------


def test_deps_status_clean_for_types_bundled_in_the_base_install():
    """Types with no optional library report ok with an empty hint."""
    for ctype in ("gemini", "openai", "openai_responses", "openai_subscription", "codex"):
        assert deps_status(ctype) == {"ok": True, "extra": "", "install": ""}


def test_deps_status_names_the_extra_when_the_library_is_absent():
    assert deps_status("ollama", extras=ABSENT_EXTRAS) == {
        "ok": False,
        "extra": "ollama",
        "install": 'pip install "ag2-assistant[ollama]"',
    }
    assert deps_status("anthropic", extras=ABSENT_EXTRAS)["install"] == (
        'pip install "ag2-assistant[anthropic]"'
    )
    assert deps_status("ollama", extras=PRESENT_EXTRAS)["ok"] is True


def test_deps_status_probes_the_real_provider_libraries_by_default():
    """The default map names the actual libraries, so ``ok`` mirrors this install."""
    for ctype, (module, extra) in PROVIDER_EXTRA.items():
        status = deps_status(ctype)
        assert status["extra"] == extra
        assert status["ok"] is (importlib.util.find_spec(module) is not None)


def test_missing_provider_library_makes_a_config_unusable(store):
    """A missing library makes a config unusable regardless of its key state."""
    env = {"ANTHROPIC_API_KEY": "sk-test"}
    ollama = {"type": "ollama", "model": "qwen3.5:4b"}
    anthropic = {"type": "anthropic", "model": "claude-x"}

    assert store.usable(ollama, env, extras=PRESENT_EXTRAS) is True
    assert store.usable(anthropic, env, extras=PRESENT_EXTRAS) is True

    assert store.usable(ollama, env, extras=ABSENT_EXTRAS) is False
    assert store.usable(anthropic, env, extras=ABSENT_EXTRAS) is False
    # key_source still reports the key situation, not the library one.
    assert store.key_source(ollama, env) == "not_needed"


def test_module_present_never_raises_into_the_health_path():
    # A dotted name under a non-package parent makes the real find_spec raise
    # ModuleNotFoundError — the health path must read that as "absent", not blow up.
    assert _module_present("json.decoder.definitely_not_a_submodule") is False
