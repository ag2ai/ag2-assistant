"""CLI multi-profile surface (WP5): `profiles create`/`list`, per-profile
targeting for data-touching commands, and zero-profile §3.5 guidance.

The CLI is a process boundary, so the install it runs against is handed to it through
the environment the way a shell would — see the ``cli`` fixture. Nothing is patched:
click's runner restores every variable it set when the invocation ends.
"""

import asyncio

import pytest
from typer.testing import CliRunner

from assistant.cli import _DEFAULT_ACCENT, app
from assistant.memory import write_profile
from assistant.paths import Paths
from assistant.profiles import ProfileRegistry

runner = CliRunner()


@pytest.fixture
def install(tmp_path) -> Paths:
    """The disposable install layout the CLI resolves — the same one the assertions read."""
    return Paths.from_env({"AG2ASSISTANT_DATA_DIR": str(tmp_path / "state")}, tmp_path)


@pytest.fixture
def cli(tmp_path, install):
    """Invoke the CLI against ``install``: ``cli(["profiles", "create", "Work"])``.

    HOME goes along too, so anything the CLI derives from it (the Codex login, a
    fallback layout) also lands under disposable space. Both keys are passed through
    click's ``env``, which restores them afterwards — a command that writes
    AG2ASSISTANT_* into the process env cannot leak into the next test."""
    env = {"HOME": str(tmp_path), "AG2ASSISTANT_DATA_DIR": str(install.root)}

    def invoke(args):
        return runner.invoke(app, args, env=env)

    return invoke


@pytest.fixture
def registry(install) -> ProfileRegistry:
    """The registry over the same layout the CLI resolves."""
    return ProfileRegistry(install)


# --- profiles create ---


def test_create_default_accent(cli, registry):
    result = cli(["profiles", "create", "Work"])
    assert result.exit_code == 0, result.output
    # no --accent → the single fallback hex (backend keeps no palette catalogue)
    assert f"accent    {_DEFAULT_ACCENT}" in result.output
    assert "Created profile 'work'" in result.output

    meta = registry.get_profile("work")
    assert meta is not None
    assert meta.accent == _DEFAULT_ACCENT
    # first profile becomes the active default
    assert registry.load_registry()["active_default"] == "work"
    # the dir was created
    assert registry.profile_dir("work").exists()


def test_create_explicit_accent(cli, registry):
    result = cli(["profiles", "create", "Work", "--accent", "#7A52EC"])
    assert result.exit_code == 0, result.output
    # normalised to lowercase on store + echo
    assert "accent    #7a52ec" in result.output

    assert registry.get_profile("work").accent == "#7a52ec"


def test_create_duplicate_accent_allowed(cli, registry):
    # No uniqueness rule (ADR 0002): two profiles may share a colour.
    cli(["profiles", "create", "Work", "--accent", "#109e91"])
    result = cli(["profiles", "create", "Personal", "--accent", "#109e91"])
    assert result.exit_code == 0, result.output

    assert registry.get_profile("personal").accent == "#109e91"


def test_create_invalid_accent_errors(cli):
    result = cli(["profiles", "create", "Work", "--accent", "chartreuse"])
    assert result.exit_code == 1
    assert "invalid accent" in result.output


def test_create_workspace_derived(cli, install, registry):
    result = cli(["profiles", "create", "Work"])
    assert result.exit_code == 0, result.output

    # workspace is derived from the profile dir (not user-chosen) and echoed on create.
    workspace = registry.profile_dir("work") / "workspace"
    assert workspace == install.root / "profiles" / "work" / "workspace"
    assert f"workspace {workspace}" in result.output


def test_create_rejects_workspace_flag(cli):
    # The --workspace flag was removed — profiles always store under the install root.
    result = cli(["profiles", "create", "Work", "--workspace", "/tmp/my-ws"])
    assert result.exit_code != 0


# --- profiles list ---


def test_list_empty(cli):
    result = cli(["profiles", "list"])
    assert result.exit_code == 0
    assert "no profiles yet" in result.output


def test_list_marks_active_default(cli):
    cli(["profiles", "create", "Work"])  # → active default
    cli(["profiles", "create", "Personal"])
    result = cli(["profiles", "list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    work_line = next(line for line in lines if "work" in line and "Work" in line)
    personal_line = next(line for line in lines if "personal" in line)
    assert work_line.startswith("*")
    assert not personal_line.startswith("*")


def test_list_hides_archived_unless_all(cli, registry):
    cli(["profiles", "create", "Work"])
    cli(["profiles", "create", "Personal"])

    registry.archive_profile("personal")

    default = cli(["profiles", "list"])
    assert "personal" not in default.output
    assert "work" in default.output

    with_all = cli(["profiles", "list", "--all"])
    assert "personal" in with_all.output
    assert "(archived)" in with_all.output


# --- zero-profile guidance (§3.5) ---


def test_agent_zero_profiles_exits_with_guidance(cli):
    result = cli(["agent", "hello"])
    assert result.exit_code == 1
    assert "create one first" in result.output
    assert "profiles create" in result.output


def test_chat_zero_profiles_exits_with_guidance(cli):
    result = cli(["chat"])
    assert result.exit_code == 1
    assert "create one first" in result.output


def test_profile_show_zero_profiles_exits_with_guidance(cli):
    result = cli(["profile", "show"])
    assert result.exit_code == 1
    assert "create one first" in result.output


def test_unknown_profile_exits_with_guidance(cli):
    cli(["profiles", "create", "Work"])
    result = cli(["profile", "show", "-p", "ghost"])
    assert result.exit_code == 1
    assert "create one first" in result.output


def test_archived_profile_targeting_reports_archived(cli, install, registry):
    cli(["profiles", "create", "Work"])
    cli(["profiles", "create", "Personal"])

    registry.archive_profile("personal")
    result = cli(["profile", "show", "-p", "personal"])
    assert result.exit_code == 1
    assert "archived" in result.output


# --- --profile targeting reads the right store (isolation) ---


async def _seed(install, pid: str, text: str) -> None:

    d = ProfileRegistry(install).profile_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    await write_profile(text, d / "profile.db")


def test_profile_show_targets_the_named_profile(cli, install):

    cli(["profiles", "create", "Work"])
    cli(["profiles", "create", "Personal"])
    asyncio.run(_seed(install, "work", "WORK-MEMORY"))
    asyncio.run(_seed(install, "personal", "PERSONAL-MEMORY"))

    work = cli(["profile", "show", "-p", "work"])
    assert work.exit_code == 0, work.output
    assert "WORK-MEMORY" in work.output
    assert "PERSONAL-MEMORY" not in work.output

    personal = cli(["profile", "show", "-p", "personal"])
    assert personal.exit_code == 0, personal.output
    assert "PERSONAL-MEMORY" in personal.output
    assert "WORK-MEMORY" not in personal.output


def test_profile_show_defaults_to_active(cli, install):

    cli(["profiles", "create", "Work"])  # active default
    asyncio.run(_seed(install, "work", "WORK-MEMORY"))
    result = cli(["profile", "show"])  # no -p → active default
    assert result.exit_code == 0, result.output
    assert "WORK-MEMORY" in result.output


def test_permissions_are_global_and_need_no_profile(cli, install):
    """Permissions are install-wide now: `permissions` commands take no --profile,
    work with zero profiles, and a grant lands in the shared root store."""

    # zero profiles: list still works (no §3.5 guidance, no exit 1)
    empty = cli(["permissions", "list"])
    assert empty.exit_code == 0, empty.output
    assert "Allowed commands:" in empty.output

    cmd = cli(["permissions", "allow-command", "run_shell_command(git *)"])
    assert cmd.exit_code == 0, cmd.output

    listed = cli(["permissions", "list"])
    assert "run_shell_command(git *)" in listed.output

    # persisted to the shared root store, not a per-profile dir
    assert (install.root / "permissions.json").exists()

    # revoke-command hit then miss
    hit = cli(["permissions", "revoke-command", "run_shell_command(git *)"])
    assert hit.exit_code == 0 and "Revoked command" in hit.output
    miss = cli(["permissions", "revoke-command", "run_shell_command(git *)"])
    assert "Not in allow list" in miss.output


def test_permissions_allow_command_rejects_garbage(cli):
    result = cli(["permissions", "allow-command", "has spaces"])
    assert result.exit_code == 1
    assert "Cannot allow" in result.output


def test_permissions_allow_command_rejects_bare_exec_tools(cli):
    # A blanket grant on an arbitrary-execution tool would authorise everything
    # forever — the CLI must refuse shell (per-prefix only) and host code (per-run
    # approval only) alike.
    for tool in ("run_shell_command", "run_shell_local"):
        result = cli(["permissions", "allow-command", tool])
        assert result.exit_code == 1, result.output
        assert "prefix rule" in result.output
    for tool in ("run_code", "run_code_local"):
        result = cli(["permissions", "allow-command", tool])
        assert result.exit_code == 1, result.output
        assert "arbitrary code" in result.output
    listing = cli(["permissions", "list"])
    for tool in ("run_shell_command", "run_code"):
        assert tool not in listing.output


# --- folders (task-scope label + --task grant/revoke) ---


def test_folders_list_shows_task_scope_label(cli, tmp_path):
    add = cli(["folders", "add", str(tmp_path)])
    assert add.exit_code == 0, add.output
    folder_id = add.output.split()[1].rstrip(":")

    grant = cli(["folders", "grant", folder_id, "work", "--task", "task-1"])
    assert grant.exit_code == 0, grant.output
    assert "(task task-1)" in grant.output

    listing = cli(["folders", "list"])
    assert listing.exit_code == 0, listing.output
    assert "(task task-1): read" in listing.output


def test_folders_grant_and_revoke_task_scope(cli, tmp_path):
    add = cli(["folders", "add", str(tmp_path)])
    folder_id = add.output.split()[1].rstrip(":")

    grant = cli(["folders", "grant", folder_id, "work", "--task", "task-2"])
    assert grant.exit_code == 0, grant.output

    revoke = cli(["folders", "revoke", folder_id, "work", "--task", "task-2"])
    assert revoke.exit_code == 0, revoke.output
    assert "Revoked." in revoke.output

    # gone: a second revoke of the same grant is a miss
    miss = cli(["folders", "revoke", folder_id, "work", "--task", "task-2"])
    assert miss.exit_code == 1
    assert "No such grant." in miss.output


def test_folders_grant_rejects_chat_and_task_together(cli, tmp_path):
    add = cli(["folders", "add", str(tmp_path)])
    folder_id = add.output.split()[1].rstrip(":")

    result = cli(["folders", "grant", folder_id, "work", "--chat", "c1", "--task", "t1"])
    assert result.exit_code == 1
    assert "not both" in result.output


def test_data_dir_flag_redirects_root(tmp_path):
    """The flag alone redirects the install root, with AG2ASSISTANT_DATA_DIR unset —
    so it goes through the bare runner rather than the ``cli`` fixture (which would set
    the variable the flag is meant to replace)."""
    env = {"AG2ASSISTANT_DATA_DIR": None, "HOME": str(tmp_path)}
    result = runner.invoke(
        app, ["--data-dir", str(tmp_path), "profiles", "create", "Work"], env=env
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "profiles.json").exists()
    assert (tmp_path / "profiles" / "work").is_dir()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
