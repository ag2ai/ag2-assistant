"""Host coding-agent detection: a real `which` over an explicit search path."""

from pathlib import Path

from assistant.coding import detect
from tests.support.stubs import write_stub


def _bin(tmp_path: Path, *names: str) -> Path:
    """A directory holding real executable stubs for the named adapters."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        write_stub(bin_dir / name)
    return bin_dir


def test_detect_returns_all_known_agents(tmp_path):
    agents = detect.detect_agents([_bin(tmp_path)])
    assert {a.name for a in agents} == {"claude", "codex", "opencode"}


def test_availability_reflects_what_is_really_on_the_search_path(tmp_path):
    by_name = {a.name: a for a in detect.detect_agents([_bin(tmp_path, "claude-agent-acp")])}
    assert by_name["claude"].available is True
    assert Path(by_name["claude"].path).is_file()
    assert by_name["codex"].available is False
    assert by_name["codex"].path is None
    assert by_name["opencode"].available is False


def test_available_agents_filters(tmp_path):
    names = [a.name for a in detect.available_agents([_bin(tmp_path, "opencode")])]
    assert names == ["opencode"]


def test_an_empty_search_path_finds_nothing(tmp_path):
    """No implicit fallback to the process PATH — an empty path means empty."""
    assert detect.available_agents([]) == []


def test_resolve_explicit_agent(tmp_path):
    resolved = detect.resolve_agent("codex", [_bin(tmp_path, "codex-acp")])
    assert resolved is not None and resolved.name == "codex"


def test_resolve_explicit_unavailable_is_none(tmp_path):
    assert detect.resolve_agent("codex", [_bin(tmp_path)]) is None


def test_resolve_empty_picks_first_available(tmp_path):
    resolved = detect.resolve_agent("", [_bin(tmp_path, "opencode", "codex-acp")])
    assert resolved is not None and resolved.available is True


def test_resolve_unknown_name_is_none(tmp_path):
    assert detect.resolve_agent("nope", [_bin(tmp_path, "opencode")]) is None


def test_opencode_command_includes_acp_subcommand(tmp_path):
    bin_dir = _bin(tmp_path, "opencode")
    resolved = detect.resolve_agent("opencode", [bin_dir])
    assert resolved.command == [str(bin_dir / "opencode"), "acp"]


def test_the_launch_command_is_the_resolved_executable(tmp_path):
    """Spawning must not re-resolve the name: a child with a different PATH would
    otherwise launch a different adapter than the one we detected."""
    bin_dir = _bin(tmp_path, "codex-acp")
    resolved = detect.resolve_agent("codex", [bin_dir])
    assert resolved.command == [str(bin_dir / "codex-acp")]
    assert resolved.command[0] == resolved.path


def test_an_unavailable_agent_keeps_the_bare_command(tmp_path):
    by_name = {a.name: a for a in detect.detect_agents([_bin(tmp_path)])}
    assert by_name["opencode"].command == ["opencode", "acp"]


def test_adapter_present_is_a_thin_yes_no(tmp_path):
    bin_dir = _bin(tmp_path, "claude-agent-acp")
    assert detect.adapter_present("claude", [bin_dir]) is True
    assert detect.adapter_present("codex", [bin_dir]) is False
    assert detect.adapter_present("nope", [bin_dir]) is False


def test_an_earlier_search_path_entry_wins(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_stub(first / "codex-acp", stdout="first")
    write_stub(second / "codex-acp", stdout="second")
    resolved = detect.resolve_agent("codex", [first, second])
    assert resolved is not None
    assert Path(resolved.path).parent == first


def test_a_non_executable_file_does_not_count_as_available(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex-acp").write_text("#!/bin/sh\n")  # no chmod +x
    assert detect.adapter_present("codex", [bin_dir]) is False


def test_default_search_path_splits_the_given_path_variable():
    assert detect.default_search_path({"PATH": "/a:/b"}) == [Path("/a"), Path("/b")]
    assert detect.default_search_path({}) == []


# ---- host bridge ---------------------------------------------------------------


def test_parse_bridge_reads_host_port_and_token():
    ep = detect.parse_bridge("host.docker.internal:9001", "s3cret")
    assert (ep.host, ep.port, ep.token) == ("host.docker.internal", 9001, "s3cret")


def test_parse_bridge_bare_host_defaults_the_port():
    ep = detect.parse_bridge("myhost")
    assert (ep.host, ep.port, ep.token) == ("myhost", detect.DEFAULT_PORT, "")


def test_parse_bridge_bad_port_falls_back_to_the_default():
    assert detect.parse_bridge("myhost:nope").port == detect.DEFAULT_PORT


def test_parse_bridge_unset_means_local_subprocess_mode():
    assert detect.parse_bridge("") is None
    assert detect.parse_bridge("   ") is None


def test_bridge_endpoint_reads_only_the_env_it_is_given():
    env = {"AG2ASSISTANT_ACP_BRIDGE": "1.2.3.4:9", "AG2ASSISTANT_ACP_BRIDGE_TOKEN": "t"}
    ep = detect.bridge_endpoint(env)
    assert (ep.host, ep.port, ep.token) == ("1.2.3.4", 9, "t")
    assert detect.bridge_endpoint({}) is None
