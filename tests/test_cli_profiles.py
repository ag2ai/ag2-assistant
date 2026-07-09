"""CLI multi-profile surface (WP5): `profiles create`/`list`, per-profile
targeting for data-touching commands, and zero-profile §3.5 guidance.

The autouse conftest fixture points HOME at a tmp dir, so the registry, profile
dirs, and stores resolve under disposable space.
"""

import pytest
from typer.testing import CliRunner

from assistant.cli import app
from assistant.profiles import PALETTES

runner = CliRunner()


# --- profiles create ---


def test_create_default_palette_picks_first_unused():
    result = runner.invoke(app, ["profiles", "create", "Work"])
    assert result.exit_code == 0, result.output
    # first PALETTES entry with nothing claimed yet
    assert f"palette   {PALETTES[0]}" in result.output
    assert "Created profile 'work'" in result.output

    from assistant import profiles

    meta = profiles.get_profile("work")
    assert meta is not None
    assert meta.palette == PALETTES[0]
    # first profile becomes the active default
    assert profiles.load_registry()["active_default"] == "work"
    # the dir was created
    assert profiles.profile_dir("work").exists()


def test_create_second_default_palette_skips_used():
    runner.invoke(app, ["profiles", "create", "Work"])
    result = runner.invoke(app, ["profiles", "create", "Personal"])
    assert result.exit_code == 0, result.output
    # second profile takes the SECOND palette (first is claimed)
    assert f"palette   {PALETTES[1]}" in result.output


def test_create_explicit_palette():
    result = runner.invoke(app, ["profiles", "create", "Work", "--palette", "violet"])
    assert result.exit_code == 0, result.output
    assert "palette   violet" in result.output

    from assistant import profiles

    assert profiles.get_profile("work").palette == "violet"


def test_create_duplicate_palette_errors():
    runner.invoke(app, ["profiles", "create", "Work", "--palette", "teal"])
    result = runner.invoke(app, ["profiles", "create", "Personal", "--palette", "teal"])
    assert result.exit_code == 1
    assert "palette already in use" in result.output


def test_create_invalid_palette_errors():
    result = runner.invoke(app, ["profiles", "create", "Work", "--palette", "chartreuse"])
    assert result.exit_code == 1
    assert "invalid palette" in result.output


def test_create_workspace_default():
    result = runner.invoke(app, ["profiles", "create", "Work"])
    assert result.exit_code == 0, result.output

    from assistant import profiles

    meta = profiles.get_profile("work")
    # default workspace ends with ~/Documents/AG2 Assistant/<Name>
    assert meta.workspace.endswith("Documents/AG2 Assistant/Work")
    assert f"workspace {meta.workspace}" in result.output


def test_create_explicit_workspace():
    result = runner.invoke(app, ["profiles", "create", "Work", "--workspace", "/tmp/my-ws"])
    assert result.exit_code == 0, result.output
    assert "workspace /tmp/my-ws" in result.output

    from assistant import profiles

    assert profiles.get_profile("work").workspace == "/tmp/my-ws"


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
    from assistant import profiles

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
    from assistant import profiles

    profiles.archive_profile("personal")
    result = runner.invoke(app, ["profile", "show", "-p", "personal"])
    assert result.exit_code == 1
    assert "archived" in result.output


# --- --profile targeting reads the right store (isolation) ---


async def _seed(pid: str, text: str) -> None:
    from assistant import profiles
    from assistant.memory import write_profile

    d = profiles.profile_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    await write_profile(text, d / "profile.db")


def test_profile_show_targets_the_named_profile():
    import asyncio

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
    import asyncio

    runner.invoke(app, ["profiles", "create", "Work"])  # active default
    asyncio.run(_seed("work", "WORK-MEMORY"))
    result = runner.invoke(app, ["profile", "show"])  # no -p → active default
    assert result.exit_code == 0, result.output
    assert "WORK-MEMORY" in result.output


def test_permissions_are_global_and_need_no_profile():
    """Permissions are install-wide now: `permissions` commands take no --profile,
    work with zero profiles, and a grant lands in the shared root store."""
    from assistant.config import load_config

    # zero profiles: list still works (no §3.5 guidance, no exit 1)
    empty = runner.invoke(app, ["permissions", "list"])
    assert empty.exit_code == 0, empty.output
    assert "Allowed folders:" in empty.output
    assert "Allowed commands:" in empty.output

    allow = runner.invoke(app, ["permissions", "allow", "/tmp/work-repo"])
    assert allow.exit_code == 0, allow.output
    cmd = runner.invoke(app, ["permissions", "allow-command", "run_shell_command(git *)"])
    assert cmd.exit_code == 0, cmd.output

    listed = runner.invoke(app, ["permissions", "list"])
    assert "/tmp/work-repo" in listed.output
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
