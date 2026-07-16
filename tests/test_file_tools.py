"""Host file tools: list_folder (read-gated) and write_file (write-gated)."""

from assistant.folders import READ, READ_WRITE, FolderStore
from assistant.permissions import PermissionManager, PermissionStore
from assistant.tools.files import list_folder_impl, write_file_impl


def _manager(tmp_path, workspace=None):
    return PermissionManager(
        PermissionStore(path=tmp_path / "perm.json"),
        asker=None,  # no prompting: only pre-granted access works
        folders=FolderStore(path=tmp_path / "folders.json"),
        profile="p1",
        workspace_dir=workspace,
    )


async def test_list_folder_requires_read_grant(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    (d / "b.txt").write_text("x")
    (d / "sub").mkdir()
    m = _manager(tmp_path)
    out = await list_folder_impl(str(d), m)
    assert "don't have permission" in out
    FolderStore(path=tmp_path / "folders.json").grant_path(d, READ, "p1")
    out = await list_folder_impl(str(d), m)
    assert "sub/" in out and "b.txt" in out


async def test_write_file_requires_write_grant(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    m = _manager(tmp_path)
    store = FolderStore(path=tmp_path / "folders.json")
    store.grant_path(d, READ, "p1")  # read is NOT enough
    out = await write_file_impl(str(d / "f.txt"), "hello", m)
    assert "write permission" in out and not (d / "f.txt").exists()
    store.grant_path(d, READ_WRITE, "p1")
    out = await write_file_impl(str(d / "f.txt"), "hello", m)
    assert (d / "f.txt").read_text() == "hello"


async def test_write_file_relative_lands_in_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m = _manager(tmp_path, workspace=ws)
    out = await write_file_impl("notes/a.md", "hi", m)
    assert (ws / "notes" / "a.md").read_text() == "hi"
    assert "a.md" in out


async def test_write_file_relative_escape_needs_grant(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "outside").mkdir()
    m = _manager(tmp_path, workspace=ws)
    out = await write_file_impl("../outside/x.txt", "no", m)
    assert "write permission" in out and not (tmp_path / "outside" / "x.txt").exists()


def test_toolkit_write_file_dropped_for_host_tool(tmp_path):
    """With a workspace present (toolkit branch active), build_agent_tools must
    expose exactly one write_file (the host one) and no toolkit read_file."""
    from assistant.tools import build_agent_tools

    tools = build_agent_tools(capabilities=["files"], workspace_dir=str(tmp_path))
    names = [t.name if hasattr(t, "name") else t.__name__ for t in tools]
    assert names.count("write_file") == 1
    assert names.count("read_file") == 1
    assert "list_folder" in names
    assert "update_file" in names  # the rest of the workspace toolkit survives
