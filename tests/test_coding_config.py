"""Per-agent ACP config building (assistant.coding.config)."""

import pytest
from ag2.acp import ClaudeCodeConfig, CodexConfig, OpenCodeConfig

from assistant.coding import config as cfgmod
from assistant.coding.detect import AgentInfo


def _agent(name, command):
    return AgentInfo(name=name, label=name, command=command, available=True, path="/x")


@pytest.mark.parametrize(
    "name,command,expected_cls",
    [
        ("claude", ["claude-agent-acp"], ClaudeCodeConfig),
        ("codex", ["codex-acp"], CodexConfig),
        ("opencode", ["opencode", "acp"], OpenCodeConfig),
    ],
)
def test_build_config_type_and_fields(tmp_path, name, command, expected_cls):
    cfg = cfgmod.build_config(_agent(name, command), str(tmp_path))
    assert isinstance(cfg, expected_cls)
    assert cfg.cwd == str(tmp_path)
    assert cfg.fs_root == str(tmp_path)  # writes confined to the working dir
    assert cfg.command == command


def test_default_permission_policy_is_ask(tmp_path):
    cfg = cfgmod.build_config(_agent("claude", ["claude-agent-acp"]), str(tmp_path))
    assert cfg.permission_policy == "ask"


def test_permission_policy_override(tmp_path):
    cfg = cfgmod.build_config(
        _agent("claude", ["claude-agent-acp"]), str(tmp_path), permission_policy="auto"
    )
    assert cfg.permission_policy == "auto"


def test_no_api_keys_injected(tmp_path):
    # Auth is the CLI's on-disk login; we never pass provider keys through env.
    cfg = cfgmod.build_config(_agent("codex", ["codex-acp"]), str(tmp_path))
    env = cfg.env or {}
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env


def test_turn_timeout_is_bounded_by_default(tmp_path):
    # A stuck agent must not hang the chat turn forever.
    cfg = cfgmod.build_config(_agent("opencode", ["opencode", "acp"]), str(tmp_path))
    assert cfg.turn_timeout is not None
    assert cfg.turn_timeout > 0


def test_unknown_name_falls_back_to_base_config(tmp_path):
    cfg = cfgmod.build_config(_agent("weird", ["weird-acp"]), str(tmp_path))
    assert cfg.command == ["weird-acp"]
    assert cfg.cwd == str(tmp_path)
