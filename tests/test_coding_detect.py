"""Host coding-agent detection (assistant.coding.detect)."""

from assistant.coding import detect


def _fake_which(available):
    """A shutil.which stand-in that resolves only the names in `available`."""
    return lambda name: f"/usr/local/bin/{name}" if name in available else None


def test_detect_returns_all_known_agents(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which(set()))
    agents = detect.detect_agents()
    assert {a.name for a in agents} == {"claude", "codex", "opencode"}


def test_availability_reflects_which(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which({"claude-agent-acp"}))
    by_name = {a.name: a for a in detect.detect_agents()}
    assert by_name["claude"].available is True
    assert by_name["codex"].available is False
    assert by_name["opencode"].available is False


def test_available_agents_filters(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which({"opencode"}))
    names = [a.name for a in detect.available_agents()]
    assert names == ["opencode"]


def test_resolve_explicit_agent(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which({"codex-acp"}))
    resolved = detect.resolve_agent("codex")
    assert resolved is not None
    assert resolved.name == "codex"


def test_resolve_explicit_unavailable_is_none(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which(set()))
    assert detect.resolve_agent("codex") is None


def test_resolve_empty_picks_first_available(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which({"opencode", "codex-acp"}))
    resolved = detect.resolve_agent("")
    assert resolved is not None
    assert resolved.available is True


def test_resolve_unknown_name_is_none(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which({"claude-agent-acp"}))
    assert detect.resolve_agent("nonesuch") is None


def test_opencode_command_includes_acp_subcommand(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", _fake_which({"opencode"}))
    resolved = detect.resolve_agent("opencode")
    assert resolved.command == ["opencode", "acp"]
