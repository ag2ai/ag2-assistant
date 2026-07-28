"""Tests for the claude_code main-model ACP provider config."""

from assistant.coding import acp_provider
from assistant.coding.detect import BridgeEndpoint
from assistant.config import Config


def _cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.workspace_dir = tmp_path
    return cfg


def test_build_local_config(tmp_path, monkeypatch):
    monkeypatch.setattr(acp_provider.detect, "bridge_endpoint", lambda: None)
    c = acp_provider.build_model_config(_cfg(tmp_path), model="sonnet")
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


def test_no_model_means_cli_default(tmp_path, monkeypatch):
    monkeypatch.setattr(acp_provider.detect, "bridge_endpoint", lambda: None)
    c = acp_provider.build_model_config(_cfg(tmp_path))
    # No model in the entry → no ANTHROPIC_MODEL: the CLI's own settings apply.
    assert c.env is None
    assert c.model is None


def test_options_override(tmp_path, monkeypatch):
    monkeypatch.setattr(acp_provider.detect, "bridge_endpoint", lambda: None)
    c = acp_provider.build_model_config(
        _cfg(tmp_path), model="sonnet", options={"turn_timeout": 60.0}
    )
    assert c.turn_timeout == 60.0


def test_options_env_merges_over_model_env(tmp_path, monkeypatch):
    monkeypatch.setattr(acp_provider.detect, "bridge_endpoint", lambda: None)
    c = acp_provider.build_model_config(
        _cfg(tmp_path), model="sonnet", options={"env": {"CLAUDE_CONFIG_DIR": "/x"}}
    )
    # An options env must not erase the model selection — merge, options win per key.
    assert c.env == {"ANTHROPIC_MODEL": "sonnet", "CLAUDE_CONFIG_DIR": "/x"}


def test_bridge_mode_disables_tool_exposure(tmp_path, monkeypatch):
    ep = BridgeEndpoint(host="host.docker.internal", port=8801, token="t")
    monkeypatch.setattr(acp_provider.detect, "bridge_endpoint", lambda: ep)
    made = {}

    def fake_connector(endpoint, name, directory):
        made["args"] = (endpoint, name, directory)
        return object()

    monkeypatch.setattr(acp_provider.bridge_client, "make_connector", fake_connector)
    c = acp_provider.build_model_config(_cfg(tmp_path), model="sonnet")
    # The MCP tool gateway binds 127.0.0.1 in THIS process; a host-side CLI
    # reached over the bridge can't connect to it, so exposure must be off.
    assert c.expose_tools is False
    assert made["args"] == (ep, "claude", str(tmp_path))
