"""Provider-native tool registry: availability per type, capability gating, and
the suppression that replaces the old _NATIVE_WEB_FETCH_PROVIDERS branch."""

import json

import ag2
import pytest
from ag2.tools import CodeExecutionTool, WebFetchTool, WebSearchTool

from assistant import llm_configs
from assistant.agent import model_config
from assistant.builtin_tools import (
    BuiltinTool,
    active_builtins,
    build_builtin_tools,
    builtin_ids_for,
    builtin_tools_for,
    find_builtin,
    suppressed_by,
)
from assistant.config import Config, update_global_section
from assistant.tools import CAPABILITIES, build_agent_tools
from tests.support.http import async_client, recording_responder


def names(tools):
    return [getattr(t, "name", "") for t in tools]


# --- the registry ------------------------------------------------------------


@pytest.mark.parametrize(
    "ctype,expected",
    [
        ("anthropic", ("web_search", "web_fetch", "code_execution")),
        ("openai_responses", ("web_search", "code_execution")),
        ("gemini", ("web_search", "web_fetch", "code_execution")),
        ("openai", ()),
        ("openai_subscription", ()),
        ("ollama", ()),
        ("claude_code", ()),
        ("codex", ()),
    ],
)
def test_each_type_offers_exactly_its_own_tools(ctype, expected):
    assert builtin_ids_for(ctype) == expected


def test_every_config_type_is_registered():
    """A new type cannot be added without deciding its provider tools: the lookup
    is total over TYPES, so an unregistered one would read as a silent ()."""
    from assistant.builtin_tools import _REGISTRY

    assert set(llm_configs.TYPES) <= set(_REGISTRY)


def test_an_unknown_type_offers_nothing_rather_than_raising():
    assert builtin_ids_for("no_such_type") == ()
    assert find_builtin("no_such_type", "web_search") is None


def test_the_registry_is_injectable_like_deps_status_extras():
    fake = {"anthropic": (BuiltinTool("only_this", WebSearchTool),)}
    assert [t.id for t in builtin_tools_for("anthropic", registry=fake)] == ["only_this"]


def test_the_same_id_is_a_different_tool_under_each_provider():
    """web_fetch is Anthropic's citing fetcher and Gemini's url_context — one id,
    separate registrations, so neither carries the other's assumptions."""
    a = find_builtin("anthropic", "web_fetch")
    g = find_builtin("gemini", "web_fetch")
    assert a is not None and g is not None and a is not g


@pytest.mark.parametrize("ctype", ["anthropic", "openai_responses", "gemini"])
def test_registered_factories_are_the_ag2_classes(ctype):
    """Pins the §1 support tables: if an AG2 upgrade drops a mapper, the class we
    hand it is the thing to re-check."""
    allowed = {WebSearchTool, WebFetchTool, CodeExecutionTool}
    assert {t.factory for t in builtin_tools_for(ctype)} <= allowed


@pytest.mark.parametrize("ctype", ["anthropic", "openai_responses", "gemini"])
def test_every_tool_declares_a_real_capability_group(ctype):
    for spec in builtin_tools_for(ctype):
        assert spec.capability in CAPABILITIES


def test_suppressed_names_match_real_local_tools():
    """A typo in `suppresses` would silently suppress nothing, leaving the model
    with two tools for one job — so check the names against a real toolset."""
    local = set(names(build_agent_tools("", capabilities=list(CAPABILITIES), config=None)))
    for ctype in llm_configs.TYPES:
        for spec in builtin_tools_for(ctype):
            assert set(spec.suppresses) <= local, (ctype, spec.id)


# --- selection ---------------------------------------------------------------


def test_active_builtins_ignores_ids_this_type_does_not_offer():
    """A hand-edited config.yaml (or a stale client) must not reach the provider."""
    assert active_builtins("openai_responses", {"web_fetch": {}}) == []
    assert active_builtins("openai", {"web_search": {}}) == []


def test_active_builtins_respects_capability_scoping():
    enabled = {"web_search": {}, "code_execution": {}}
    assert [s.id for s, _ in active_builtins("gemini", enabled, ["web"])] == ["web_search"]
    assert [s.id for s, _ in active_builtins("gemini", enabled, ["code"])] == ["code_execution"]
    assert active_builtins("gemini", enabled, ["files"]) == []
    # None means "everything", the chat case.
    assert len(active_builtins("gemini", enabled, None)) == 2


def test_build_and_suppress_read_the_same_selection():
    active = active_builtins("gemini", {"web_search": {}, "web_fetch": {}})
    assert suppressed_by(active) == {"duckduckgo_search", "web_fetch"}
    assert len(build_builtin_tools(active)) == 2


def test_code_execution_adds_a_runner_rather_than_replacing_ours():
    """Ours reaches the user's real files under approval; the provider's cannot,
    so they are not substitutes."""
    assert suppressed_by(active_builtins("anthropic", {"code_execution": {}})) == set()


# --- wiring through build_agent_tools ---------------------------------------


def test_a_builtin_replaces_the_local_tool_it_stands_in_for():
    off = names(build_agent_tools("gemini", capabilities=["web"]))
    assert "duckduckgo_search" in off and "web_fetch" in off

    on = names(build_agent_tools("gemini", capabilities=["web"], builtin={"web_search": {}}))
    assert "duckduckgo_search" not in on
    assert "web_search" in on
    assert "web_fetch" in on  # untouched: a different switch


def test_a_task_scoped_without_web_gets_no_provider_search_either():
    tools = names(build_agent_tools("gemini", capabilities=["files"], builtin={"web_search": {}}))
    assert "web_search" not in tools
    assert "duckduckgo_search" not in tools


def test_a_type_with_no_builtins_is_unaffected_by_a_stale_selection():
    plain = names(build_agent_tools("openai", capabilities=["web"]))
    stale = names(build_agent_tools("openai", capabilities=["web"], builtin={"web_search": {}}))
    assert plain == stale
    assert "duckduckgo_search" in stale  # ours was NOT suppressed by a phantom


def test_no_builtins_leaves_the_local_web_tools():
    """Provider builtins are opt-in; the default web tools search and fetch."""
    for ctype in ("anthropic", "gemini", "openai_responses", ""):
        assert names(build_agent_tools(ctype, capabilities=["web"])) == [
            "duckduckgo_search",
            "web_fetch",
        ]


# --- options ----------------------------------------------------------------


@pytest.mark.parametrize(
    "ctype,tool_id,expected",
    [
        ("anthropic", "web_search", ("max_uses", "allowed_domains", "blocked_domains")),
        ("anthropic", "web_fetch", ("max_uses", "allowed_domains", "blocked_domains")),
        ("anthropic", "code_execution", ()),
        ("openai_responses", "web_search", ("allowed_domains",)),
        ("gemini", "web_search", ()),
        ("gemini", "web_fetch", ()),
    ],
)
def test_each_tool_offers_only_the_options_its_provider_api_supports(ctype, tool_id, expected):
    assert find_builtin(ctype, tool_id).fields == expected


async def test_a_saved_option_is_on_the_provider_tool_the_agent_is_built_with():
    tools = build_agent_tools(
        "anthropic",
        capabilities=["web"],
        builtin={"web_search": {"max_uses": 3, "blocked_domains": ["spam.example"]}},
    )
    search = next(t for t in tools if getattr(t, "name", "") == "web_search")
    # Options are plain values, so the schema resolves without a conversation context.
    (schema,) = await search.schemas(None)
    assert schema.max_uses == 3
    assert schema.blocked_domains == ["spam.example"]
    assert schema.allowed_domains is None


@pytest.mark.parametrize(
    "ctype,tool_id,options,expected",
    [
        (
            "anthropic",
            "web_fetch",
            {"max_content_tokens": 1024, "citations": False, "max_uses": 3},
            {"max_content_tokens": 1024, "citations": False, "max_uses": 3},
        ),
        (
            "openai_responses",
            "web_search",
            {"search_context_size": "high", "allowed_domains": ["example.com"]},
            {"search_context_size": "high", "allowed_domains": ["example.com"]},
        ),
        (
            "anthropic",
            "code_execution",
            {"version": "code_execution_20260120"},
            {"version": "code_execution_20260120"},
        ),
    ],
)
async def test_persisted_options_without_ui_controls_reach_the_provider_tool(
    paths, ctype, tool_id, options, expected
):
    store = llm_configs.LlmConfigStore(paths)
    entry = {
        "id": "c_existing",
        "name": "M",
        "type": ctype,
        "model": "m",
        "builtin_tools": {tool_id: options},
    }
    update_global_section(paths, "llm_configs", {"configs": [entry], "active": None})
    for _ in range(2):
        saved = store.get_config(entry["id"])
        assert saved["builtin_tools"] == {tool_id: options}
        tools = build_agent_tools(ctype, builtin=saved["builtin_tools"])
        tool = next(t for t in tools if getattr(t, "name", "") == tool_id)
        (schema,) = await tool.schemas(None)
        for name, value in expected.items():
            assert getattr(schema, name) == value
        store.save_config(saved)


def test_a_hand_edited_option_the_type_does_not_honour_never_reaches_the_tool():
    """max_uses is Anthropic's; the Responses registration does not declare it."""
    stored = {"web_search": {"max_uses": 3, "allowed_domains": ["example.com"], "invented": 1}}
    ((_, options),) = active_builtins("openai_responses", stored)
    assert options == {"allowed_domains": ["example.com"]}


def test_a_malformed_usage_limit_keeps_valid_domain_restrictions():
    stored = {"web_search": {"max_uses": "few", "blocked_domains": ["spam.example"]}}
    ((spec, options),) = active_builtins("anthropic", stored)
    assert spec.id == "web_search"
    assert options == {"blocked_domains": ["spam.example"]}


@pytest.mark.parametrize(
    "options",
    [{"blocked_domains": ["example.com"]}, [["blocked_domains", ["example.com"]]]],
)
def test_stored_gemini_domain_restrictions_never_become_unrestricted_tools(paths, options):
    entry = {
        "id": "c_gemini_filter",
        "name": "Gemini",
        "type": "gemini",
        "model": "gemini-2.5-flash",
        "builtin_tools": {"web_search": options},
    }
    update_global_section(paths, "llm_configs", {"configs": [entry], "active": None})
    cfg = Config.for_paths(paths)
    store = llm_configs.LlmConfigStore(paths)
    store.derive_onto(cfg, store.get_config(entry["id"]))
    with pytest.raises(ValueError, match="blocked_domains.*not supported"):
        build_agent_tools(cfg.llm.config_type, builtin=cfg.llm.builtin_tools, capabilities=["web"])
    assert store.get_config(entry["id"])["builtin_tools"] == entry["builtin_tools"]


@pytest.mark.parametrize("options", [{}, {"blocked_domains": []}])
async def test_gemini_search_defaults_reach_the_developer_api(paths, options):
    store = llm_configs.LlmConfigStore(paths)
    entry = store.save_config(
        {
            "name": "Gemini",
            "type": "gemini",
            "model": "gemini-2.5-flash",
            "builtin_tools": {"web_search": options},
        }
    )
    cfg = Config.for_paths(paths)
    store.derive_onto(cfg, entry)
    handler, sent = recording_responder(
        {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "PONG"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }
    )
    async with async_client(handler) as network:
        provider = model_config(cfg).copy(api_key="fake-key", streaming=False, http_client=network)
        tools = build_agent_tools("gemini", builtin=cfg.llm.builtin_tools, capabilities=["web"])
        reply = await ag2.Agent("test", config=provider, tools=tools).ask("Reply PONG")
    assert reply.body == "PONG"
    sent_tools = json.loads(sent[0]["body"])["tools"]
    assert [tool["googleSearch"] for tool in sent_tools if "googleSearch" in tool] == [{}]


@pytest.mark.parametrize("tool_id", ["web_search", "web_fetch"])
@pytest.mark.parametrize("option", ["allowed_domains", "blocked_domains"])
@pytest.mark.parametrize("domain", ["https://example.com", "*.example.com"])
def test_invalid_stored_domain_filters_never_become_unrestricted_tools(tool_id, option, domain):
    with pytest.raises(ValueError, match=option):
        build_agent_tools("anthropic", builtin={tool_id: {option: [domain]}})


def test_conflicting_stored_domain_filters_never_become_an_unrestricted_tool():
    with pytest.raises(ValueError, match="allowed_domains"):
        build_agent_tools(
            "anthropic",
            builtin={
                "web_search": {
                    "allowed_domains": ["example.com"],
                    "blocked_domains": ["spam.example"],
                }
            },
        )


@pytest.mark.parametrize(
    "domains",
    [
        ["https://example.com"],
        ["*.example.com"],
        ["example.com/blog"],
        [f"site{i}.example.com" for i in range(101)],
    ],
)
def test_invalid_responses_domain_filters_never_become_unrestricted_tools(domains):
    with pytest.raises(ValueError, match="allowed_domains"):
        build_agent_tools(
            "openai_responses",
            builtin={"web_search": {"allowed_domains": domains}},
            capabilities=["web"],
        )


async def test_responses_passes_the_maximum_valid_domain_filter_to_the_tool():
    domains = [f"site{i}.example.com" for i in range(100)]
    tools = build_agent_tools(
        "openai_responses",
        builtin={"web_search": {"allowed_domains": domains}},
        capabilities=["web"],
    )
    search = next(tool for tool in tools if getattr(tool, "name", "") == "web_search")
    (schema,) = await search.schemas(None)
    assert schema.allowed_domains == domains


@pytest.mark.parametrize("tool_id", ["web_search", "web_fetch"])
@pytest.mark.parametrize("option", ["allowed_domains", "blocked_domains"])
async def test_persisted_option_pairs_keep_their_domain_restrictions(paths, tool_id, option):
    entry = {
        "id": "c_pairs",
        "name": "M",
        "type": "anthropic",
        "model": "m",
        "builtin_tools": {tool_id: [[option, ["example.com"]], ["max_uses", 3]]},
    }
    update_global_section(paths, "llm_configs", {"configs": [entry], "active": None})
    store = llm_configs.LlmConfigStore(paths)
    for _ in range(2):
        config = Config.for_paths(paths)
        store.derive_onto(config, store.get_config(entry["id"]))
        tools = build_agent_tools(
            config.llm.config_type, builtin=config.llm.builtin_tools, capabilities=["web"]
        )
        tool = next(t for t in tools if getattr(t, "name", "") == tool_id)
        (schema,) = await tool.schemas(None)
        assert getattr(schema, option) == ["example.com"]
        assert schema.max_uses == 3


@pytest.mark.parametrize(
    "options",
    [
        "allowed_domains=example.com",
        1,
        True,
        ["allowed_domains"],
        [["allowed_domains"]],
        [["allowed_domains", ["example.com"], "extra"]],
    ],
)
def test_malformed_stored_options_never_become_an_unrestricted_tool(options):
    with pytest.raises(ValueError, match="options"):
        build_agent_tools("anthropic", builtin={"web_fetch": options}, capabilities=["web"])


@pytest.mark.parametrize("options", [None, {}, []])
async def test_empty_stored_options_keep_the_provider_defaults(options):
    tools = build_agent_tools("anthropic", builtin={"web_fetch": options}, capabilities=["web"])
    fetch = next(t for t in tools if getattr(t, "name", "") == "web_fetch")
    (schema,) = await fetch.schemas(None)
    assert schema.allowed_domains is None
    assert schema.blocked_domains is None
