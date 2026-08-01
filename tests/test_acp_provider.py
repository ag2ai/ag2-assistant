"""Tests for the main-model ACP provider configs (claude_code + codex)."""

import json

from assistant.coding import acp_provider
from assistant.config import Config
from tests.support.apps import make_paths

_BRIDGE = "host.docker.internal:8801"


def _cfg(tmp_path, *, bridge: str = "") -> Config:
    """A config whose workspace is tmp_path; ``bridge`` puts it in host-bridge mode."""
    cfg = Config.for_paths(make_paths(tmp_path), acp_bridge=bridge, acp_bridge_token="t")
    cfg.workspace_dir = tmp_path
    return cfg


class RecordingConnector:
    """Stands in for bridge_client.make_connector, recording what it was handed."""

    def __init__(self):
        self.args = None

    def __call__(self, endpoint, name, directory):
        self.args = (endpoint, name, directory)
        return object()


def test_build_local_config(tmp_path):
    c = acp_provider.build_claude_config(_cfg(tmp_path), model="sonnet")
    assert c.command == ["claude-agent-acp"]
    assert c.cwd == str(tmp_path)
    assert c.fs_root == str(tmp_path)
    assert c.model == "sonnet"
    # ACPConfig.model is response metadata only — the adapter selects its model
    # from the ANTHROPIC_MODEL env var, so the entry's model must ride there too.
    assert c.env == {"ANTHROPIC_MODEL": "sonnet"}
    assert c.permission_policy == "ask"
    assert c.expose_tools is True
    assert c.turn_timeout == acp_provider.DEFAULT_TURN_TIMEOUT


def test_no_model_means_cli_default(tmp_path):
    c = acp_provider.build_claude_config(_cfg(tmp_path))
    # No model in the entry → no ANTHROPIC_MODEL: the CLI's own settings apply.
    assert c.env is None
    assert c.model is None


def test_options_override(tmp_path):
    c = acp_provider.build_claude_config(
        _cfg(tmp_path), model="sonnet", options={"turn_timeout": 60.0}
    )
    assert c.turn_timeout == 60.0


def test_options_env_merges_over_model_env(tmp_path):
    c = acp_provider.build_claude_config(
        _cfg(tmp_path), model="sonnet", options={"env": {"CLAUDE_CONFIG_DIR": "/x"}}
    )
    # An options env must not erase the model selection — merge, options win per key.
    assert c.env == {"ANTHROPIC_MODEL": "sonnet", "CLAUDE_CONFIG_DIR": "/x"}


def test_bridge_mode_disables_tool_exposure(tmp_path):
    connector = RecordingConnector()
    c = acp_provider.build_claude_config(
        _cfg(tmp_path, bridge=_BRIDGE), model="sonnet", connector_factory=connector
    )
    # The MCP tool gateway binds 127.0.0.1 in THIS process; a host-side CLI
    # reached over the bridge can't connect to it, so exposure must be off.
    assert c.expose_tools is False
    endpoint, name, directory = connector.args
    assert (endpoint.host, endpoint.port, endpoint.token) == ("host.docker.internal", 8801, "t")
    assert (name, directory) == ("claude", str(tmp_path))


def test_build_codex_local_config(tmp_path):
    c = acp_provider.build_codex_config(_cfg(tmp_path), model="gpt-5.6-sol[medium]")
    assert c.command == ["codex-acp"]
    assert c.cwd == str(tmp_path)
    assert c.fs_root == str(tmp_path)
    assert c.permission_policy == "ask"
    assert c.expose_tools is True
    assert c.turn_timeout == acp_provider.DEFAULT_TURN_TIMEOUT
    # The adapter's model ids carry the reasoning effort in brackets; Codex config
    # wants them split. The builder parses "name[effort]" into the CODEX_CONFIG env.
    assert json.loads(c.env["CODEX_CONFIG"]) == {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "medium",
    }


def test_codex_model_without_effort_suffix(tmp_path):
    c = acp_provider.build_codex_config(_cfg(tmp_path), model="gpt-5.5")
    assert json.loads(c.env["CODEX_CONFIG"]) == {"model": "gpt-5.5"}


def test_codex_no_model_means_cli_default(tmp_path):
    c = acp_provider.build_codex_config(_cfg(tmp_path))
    # No model in the entry → no CODEX_CONFIG: the CLI's own default applies.
    assert c.env is None
    assert c.model is None


def test_codex_options_env_wins_over_derived_codex_config(tmp_path):
    c = acp_provider.build_codex_config(
        _cfg(tmp_path),
        model="gpt-5.6-sol[medium]",
        options={"env": {"CODEX_CONFIG": '{"model": "x"}'}},
    )
    # An explicit CODEX_CONFIG in the entry's Advanced options env overrides the
    # model-field derivation (options merge last, per key).
    assert c.env["CODEX_CONFIG"] == '{"model": "x"}'


def test_codex_bridge_mode_disables_tool_exposure(tmp_path):
    connector = RecordingConnector()
    c = acp_provider.build_codex_config(
        _cfg(tmp_path, bridge=_BRIDGE), model="gpt-5.5", connector_factory=connector
    )
    assert c.expose_tools is False
    endpoint, name, directory = connector.args
    assert (endpoint.host, endpoint.port) == ("host.docker.internal", 8801)
    assert (name, directory) == ("codex", str(tmp_path))


def test_local_mode_never_builds_a_bridge_connector(tmp_path):
    """No configured bridge → a plain local spawn, tools exposed, no connector."""
    connector = RecordingConnector()
    c = acp_provider.build_claude_config(_cfg(tmp_path), connector_factory=connector)
    assert connector.args is None
    assert c.expose_tools is True
