"""CLI multi-profile surface (WP5): `profiles create`/`list`, per-profile
targeting for data-touching commands, and zero-profile §3.5 guidance.

The autouse conftest fixture points HOME at a tmp dir, so the registry, profile
dirs, and stores resolve under disposable space.
"""

import asyncio

import pytest
from typer.testing import CliRunner

from assistant import profiles
from assistant.cli import _DEFAULT_ACCENT, app
from assistant.config import load_config
from assistant.memory import write_profile

runner = CliRunner()


# --- profiles create ---


def test_create_default_accent():
    result = runner.invoke(app, ["profiles", "create", "Work"])
    assert result.exit_code == 0, result.output
    # no --accent → the single fallback hex (backend keeps no palette catalogue)
    assert f"accent    {_DEFAULT_ACCENT}" in result.output
    assert "Created profile 'work'" in result.output

    meta = profiles.get_profile("work")
    assert meta is not None
    assert meta.accent == _DEFAULT_ACCENT
    # first profile becomes the active default
    assert profiles.load_registry()["active_default"] == "work"
    # the dir was created
    assert profiles.profile_dir("work").exists()


def test_create_explicit_accent():
    result = runner.invoke(app, ["profiles", "create", "Work", "--accent", "#7A52EC"])
    assert result.exit_code == 0, result.output
    # normalised to lowercase on store + echo
    assert "accent    #7a52ec" in result.output

    assert profiles.get_profile("work").accent == "#7a52ec"


def test_create_duplicate_accent_allowed():
    # No uniqueness rule (ADR 0002): two profiles may share a colour.
    runner.invoke(app, ["profiles", "create", "Work", "--accent", "#109e91"])
    result = runner.invoke(app, ["profiles", "create", "Personal", "--accent", "#109e91"])
    assert result.exit_code == 0, result.output

    assert profiles.get_profile("personal").accent == "#109e91"


def test_create_invalid_accent_errors():
    result = runner.invoke(app, ["profiles", "create", "Work", "--accent", "chartreuse"])
    assert result.exit_code == 1
    assert "invalid accent" in result.output


def test_create_workspace_derived():
    result = runner.invoke(app, ["profiles", "create", "Work"])
    assert result.exit_code == 0, result.output

    meta = profiles.get_profile("work")
    # workspace is derived from the profile dir (not user-chosen) and echoed on create.
    assert meta.workspace == str(profiles.profile_dir("work") / "workspace")
    assert f"workspace {meta.workspace}" in result.output


def test_create_rejects_workspace_flag():
    # The --workspace flag was removed — profiles always store under the install root.
    result = runner.invoke(app, ["profiles", "create", "Work", "--workspace", "/tmp/my-ws"])
    assert result.exit_code != 0


# --- profiles list ---


def test_list_empty():
    result = runner.invoke(app, ["profiles", "list"])
    assert result.exit_code == 0
    assert "no profiles yet" in result.output


def test_list_marks_active_default():
    runner.invoke(app, ["profiles", "create", "Work"])  # → active default
    runner.invoke(app, ["profiles", "create", "Personal"])
    result = runner.invoke(app, ["profiles", "list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    work_line = next(line for line in lines if "work" in line and "Work" in line)
    personal_line = next(line for line in lines if "personal" in line)
    assert work_line.startswith("*")
    assert not personal_line.startswith("*")


def test_list_hides_archived_unless_all():
    runner.invoke(app, ["profiles", "create", "Work"])
    runner.invoke(app, ["profiles", "create", "Personal"])

    profiles.archive_profile("personal")

    default = runner.invoke(app, ["profiles", "list"])
    assert "personal" not in default.output
    assert "work" in default.output

    with_all = runner.invoke(app, ["profiles", "list", "--all"])
    assert "personal" in with_all.output
    assert "(archived)" in with_all.output


# --- zero-profile guidance (§3.5) ---


def test_agent_zero_profiles_exits_with_guidance():
    result = runner.invoke(app, ["agent", "hello"])
    assert result.exit_code == 1
    assert "create one first" in result.output
    assert "profiles create" in result.output


def test_chat_zero_profiles_exits_with_guidance():
    result = runner.invoke(app, ["chat"])
    assert result.exit_code == 1
    assert "create one first" in result.output


def test_profile_show_zero_profiles_exits_with_guidance():
    result = runner.invoke(app, ["profile", "show"])
    assert result.exit_code == 1
    assert "create one first" in result.output


def test_unknown_profile_exits_with_guidance():
    runner.invoke(app, ["profiles", "create", "Work"])
    result = runner.invoke(app, ["profile", "show", "-p", "ghost"])
    assert result.exit_code == 1
    assert "create one first" in result.output


def test_archived_profile_targeting_reports_archived():
    runner.invoke(app, ["profiles", "create", "Work"])
    runner.invoke(app, ["profiles", "create", "Personal"])

    profiles.archive_profile("personal")
    result = runner.invoke(app, ["profile", "show", "-p", "personal"])
    assert result.exit_code == 1
    assert "archived" in result.output


# --- --profile targeting reads the right store (isolation) ---


async def _seed(pid: str, text: str) -> None:

    d = profiles.profile_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    await write_profile(text, d / "profile.db")


def test_profile_show_targets_the_named_profile():

    runner.invoke(app, ["profiles", "create", "Work"])
    runner.invoke(app, ["profiles", "create", "Personal"])
    asyncio.run(_seed("work", "WORK-MEMORY"))
    asyncio.run(_seed("personal", "PERSONAL-MEMORY"))

    work = runner.invoke(app, ["profile", "show", "-p", "work"])
    assert work.exit_code == 0, work.output
    assert "WORK-MEMORY" in work.output
    assert "PERSONAL-MEMORY" not in work.output

    personal = runner.invoke(app, ["profile", "show", "-p", "personal"])
    assert personal.exit_code == 0, personal.output
    assert "PERSONAL-MEMORY" in personal.output
    assert "WORK-MEMORY" not in personal.output


def test_profile_show_defaults_to_active():

    runner.invoke(app, ["profiles", "create", "Work"])  # active default
    asyncio.run(_seed("work", "WORK-MEMORY"))
    result = runner.invoke(app, ["profile", "show"])  # no -p → active default
    assert result.exit_code == 0, result.output
    assert "WORK-MEMORY" in result.output


def test_permissions_are_global_and_need_no_profile():
    """Permissions are install-wide now: `permissions` commands take no --profile,
    work with zero profiles, and a grant lands in the shared root store."""

    # zero profiles: list still works (no §3.5 guidance, no exit 1)
    empty = runner.invoke(app, ["permissions", "list"])
    assert empty.exit_code == 0, empty.output
    assert "Allowed commands:" in empty.output

    cmd = runner.invoke(app, ["permissions", "allow-command", "run_shell_command(git *)"])
    assert cmd.exit_code == 0, cmd.output

    listed = runner.invoke(app, ["permissions", "list"])
    assert "run_shell_command(git *)" in listed.output

    # persisted to the shared root store, not a per-profile dir
    assert (load_config().root_dir / "permissions.json").exists()

    # revoke-command hit then miss
    hit = runner.invoke(app, ["permissions", "revoke-command", "run_shell_command(git *)"])
    assert hit.exit_code == 0 and "Revoked command" in hit.output
    miss = runner.invoke(app, ["permissions", "revoke-command", "run_shell_command(git *)"])
    assert "Not in allow list" in miss.output


def test_permissions_allow_command_rejects_garbage():
    result = runner.invoke(app, ["permissions", "allow-command", "has spaces"])
    assert result.exit_code == 1
    assert "Cannot allow" in result.output


def test_permissions_allow_command_rejects_bare_exec_tools():
    # A blanket grant on an arbitrary-execution tool would authorise everything
    # forever — the CLI must refuse shell (per-prefix only) and host code (per-run
    # approval only) alike.
    for tool in ("run_shell_command", "run_shell_local"):
        result = runner.invoke(app, ["permissions", "allow-command", tool])
        assert result.exit_code == 1, result.output
        assert "prefix rule" in result.output
    for tool in ("run_code", "run_code_local"):
        result = runner.invoke(app, ["permissions", "allow-command", tool])
        assert result.exit_code == 1, result.output
        assert "arbitrary code" in result.output
    listing = runner.invoke(app, ["permissions", "list"])
    for tool in ("run_shell_command", "run_code"):
        assert tool not in listing.output


def test_data_dir_flag_redirects_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AG2ASSISTANT_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["--data-dir", str(tmp_path), "profiles", "create", "Work"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "profiles.json").exists()
    assert (tmp_path / "profiles" / "work").is_dir()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
