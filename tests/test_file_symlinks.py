"""A symlink inside the workspace is checked where it points, not where it sits."""

from pathlib import Path

import pytest

from assistant.folders import FolderStore
from assistant.permissions import DENY, PermissionManager, PermissionStore
from assistant.tools.file_edits import FileEdit
from assistant.tools.files import (
    list_folder_impl,
    read_file_impl,
    update_file_impl,
    write_file_impl,
)

SECRET = "sk-secret"


class DenyingAsker:
    """Answers Deny to every question and counts them."""

    def __init__(self):
        self.asked = 0

    async def ask(self, question, timeout=None):
        self.asked += 1
        return DENY


@pytest.fixture
def layout(tmp_path):
    """A workspace holding a file link and a dir link into a sibling secrets dir."""
    workspace, secrets = tmp_path / "workspace", tmp_path / "secrets"
    workspace.mkdir()
    secrets.mkdir()
    (secrets / "llm-key").write_text(SECRET)
    (workspace / "link").symlink_to(secrets / "llm-key")
    (workspace / "dirlink").symlink_to(secrets)
    return workspace, secrets


def _manager(tmp_path, workspace, asker):
    return PermissionManager(
        PermissionStore(path=tmp_path / "perm.json"),
        asker=asker,
        folders=FolderStore(path=tmp_path / "folders.json"),
        profile="p1",
        workspace_dir=workspace,
    )


def _secret(secrets: Path) -> str:
    return (secrets / "llm-key").read_text()


@pytest.mark.parametrize("relative", [False, True])
async def test_read_through_a_link_is_refused(tmp_path, layout, relative):
    workspace, _secrets = layout
    asker = DenyingAsker()
    path = "link" if relative else str(workspace / "link")
    out = await read_file_impl(path, _manager(tmp_path, workspace, asker))
    assert SECRET not in str(out)
    assert "denied permission" in str(out)
    assert asker.asked == 1


async def test_overwrite_through_a_link_is_refused(tmp_path, layout):
    workspace, secrets = layout
    manager = _manager(tmp_path, workspace, DenyingAsker())
    out = await write_file_impl(str(workspace / "link"), "PWNED", manager, overwrite=True)
    assert "denied write permission" in out
    assert _secret(secrets) == SECRET


async def test_update_through_a_link_is_refused(tmp_path, layout):
    workspace, secrets = layout
    manager = _manager(tmp_path, workspace, DenyingAsker())
    edit = FileEdit(oldText=SECRET, newText="PWNED")
    out = await update_file_impl(str(workspace / "link"), [edit], manager)
    assert "denied write permission" in str(out)
    assert _secret(secrets) == SECRET


async def test_list_through_a_dir_link_is_refused(tmp_path, layout):
    workspace, _secrets = layout
    out = await list_folder_impl(str(workspace / "dirlink"), _manager(tmp_path, workspace, None))
    assert "llm-key" not in out


async def test_a_plain_workspace_file_needs_no_prompt(tmp_path, layout):
    workspace, _secrets = layout
    (workspace / "notes.txt").write_text("hello")
    asker = DenyingAsker()
    out = await read_file_impl(str(workspace / "notes.txt"), _manager(tmp_path, workspace, asker))
    assert "hello" in str(out)
    assert asker.asked == 0


@pytest.mark.parametrize("write", [False, True])
async def test_check_follows_a_file_link_out_of_the_workspace(tmp_path, layout, write):
    workspace, _secrets = layout
    asker = DenyingAsker()
    manager = _manager(tmp_path, workspace, asker)
    assert await manager.check(workspace / "link", write=write) is False
    assert asker.asked == 1


async def test_a_refusal_names_where_the_link_points(tmp_path, layout):
    workspace, secrets = layout
    manager = _manager(tmp_path, workspace, DenyingAsker())
    out = await write_file_impl(str(workspace / "link"), "PWNED", manager, overwrite=True)
    assert str(secrets.resolve()) in out


async def test_a_link_loop_is_refused_without_raising(tmp_path, layout):
    workspace, _secrets = layout
    (workspace / "loopa").symlink_to(workspace / "loopb")
    (workspace / "loopb").symlink_to(workspace / "loopa")
    manager = _manager(tmp_path, workspace, DenyingAsker())
    out = await read_file_impl(str(workspace / "loopa"), manager)
    assert "symlink loop" in out.lower()
    assert await manager.check(workspace / "loopa") is False


async def test_a_dangling_link_out_of_the_workspace_creates_nothing(tmp_path, layout):
    workspace, secrets = layout
    (workspace / "dangling").symlink_to(secrets / "new.txt")
    manager = _manager(tmp_path, workspace, DenyingAsker())
    out = await write_file_impl(str(workspace / "dangling"), "PWNED", manager, overwrite=True)
    assert "denied write permission" in out
    assert not (secrets / "new.txt").exists()


async def test_a_new_file_under_a_dir_link_is_refused(tmp_path, layout):
    workspace, secrets = layout
    manager = _manager(tmp_path, workspace, DenyingAsker())
    out = await write_file_impl(str(workspace / "dirlink" / "created.txt"), "x", manager)
    assert "denied write permission" in out
    assert not (secrets / "created.txt").exists()
