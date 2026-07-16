"""Local file access — permission-gated, vision-first.

`read_file` lets the agent read a local file by path. `list_folder` lists a
folder's contents (read-gated) — it's what the retired `repo-files` MCP used to
provide. `write_file` writes a text file (write-gated for paths outside the
agent's own workspace; relative paths land in the workspace with no approval
needed). The first access to a folder requires the user's permission
(Claude-Code-style). PDFs and images are returned to the model as multimodal
content so it reads them by *vision* (works on scanned PDFs with no text
layer); text files are returned as text.
"""

from pathlib import Path

from ag2 import Context, DocumentInput, ImageInput, ToolResult, tool

from assistant.permissions import PermissionManager

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".tsv",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".xml",
    ".log",
    ".ini",
    ".toml",
    ".sh",
}
_MAX_TEXT_CHARS = 50_000


async def read_file_impl(path: str, permissions: PermissionManager) -> "ToolResult | str":
    """Permission-gated file read. Returns vision content for PDFs/images, text
    for text files, or a message string for errors/denials."""
    target = Path(path).expanduser()
    if not target.exists() or not target.is_file():
        return (
            f"File not found: {path}. Nothing exists at that path — check it with the "
            "user rather than guessing at other paths."
        )

    if not await permissions.check(target):
        return (
            f"The user denied permission to read {target.parent}. Do not try to "
            f"read {target.name} another way (e.g. shell or code) — tell the user "
            "you don't have permission to access it."
        )

    suffix = target.suffix.lower()
    if suffix == ".pdf":
        return ToolResult(DocumentInput(path=str(target)))
    if suffix in _IMAGE_SUFFIXES:
        return ToolResult(ImageInput(path=str(target)))
    if suffix in _TEXT_SUFFIXES or suffix == "":
        try:
            text = target.read_text(errors="replace")[:_MAX_TEXT_CHARS]
        except Exception as exc:
            return f"Could not read {path}: {exc}"
        return f"Contents of {target.name}:\n\n{text}"

    # Unknown/binary type: try as a document (vision) so the model can still try.
    return ToolResult(DocumentInput(path=str(target)))


@tool
async def read_file(path: str, context: Context) -> "ToolResult | str":
    """Read a local file by path. Asks the user's permission the first time a
    folder is accessed. PDFs and images are read visually; text files as text.

    Args:
        path: Absolute or ~-relative path to the file.
    """
    permissions = context.dependencies.get(PermissionManager) or PermissionManager()
    return await read_file_impl(path, permissions)


_MAX_LIST_ENTRIES = 500


async def list_folder_impl(path: str, permissions: PermissionManager) -> str:
    """Permission-gated (read) folder listing. Directories carry a trailing /."""
    target = Path(path).expanduser()
    if not target.exists() or not target.is_dir():
        return f"Folder not found: {path}. Check the path with the user rather than guessing."
    if not await permissions.check(target):
        return (
            f"The user denied permission to read {target}. Do not try to list it "
            "another way (e.g. shell or code) — tell the user you don't have "
            "permission to access it."
        )
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"Could not list {path}: {exc}"
    names = [
        (e.name + "/") if e.is_dir() else e.name
        for e in entries
        if not e.name.startswith(".")
    ]
    if not names:
        return f"{target} is empty."
    shown = names[:_MAX_LIST_ENTRIES]
    suffix = f"\n… ({len(names) - len(shown)} more entries not shown)" if len(names) > len(shown) else ""
    return f"Contents of {target}:\n" + "\n".join(shown) + suffix


@tool
async def list_folder(path: str, context: Context) -> str:
    """List a local folder's contents (directories marked with a trailing /).
    Asks the user's permission the first time a folder is accessed.

    Args:
        path: Absolute or ~-relative path to the folder.
    """
    permissions = context.dependencies.get(PermissionManager) or PermissionManager()
    return await list_folder_impl(path, permissions)


async def write_file_impl(path: str, content: str, permissions: PermissionManager) -> str:
    """Permission-gated (write) file write. Relative paths land in the agent's own
    workspace — implicitly allowed; absolute/~ paths need a read+write Grant."""
    raw = (path or "").strip()
    if not raw:
        return "write_file needs a path."
    p = Path(raw).expanduser()
    if not p.is_absolute():
        if permissions.workspace_dir is None:
            return "No workspace is available for a relative path — use an absolute path."
        p = (permissions.workspace_dir / raw).resolve()
    if not await permissions.check(p, write=True):
        return (
            f"The user denied write permission for {p.parent}. Do not try to write "
            f"{p.name} another way (e.g. shell or code) — tell the user you don't "
            "have write permission there."
        )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "")
    except OSError as exc:
        return f"Could not write {path}: {exc}"
    return f"Wrote {len(content or '')} characters to {p}."


@tool
async def write_file(path: str, content: str, context: Context) -> str:
    """Write a text file. A relative path is saved inside your own workspace (no
    approval needed). An absolute or ~ path needs the user's write permission for
    that folder — asked on first access.

    Args:
        path: Workspace-relative, absolute, or ~-relative file path.
        content: The full text content to write (replaces any existing file).
    """
    permissions = context.dependencies.get(PermissionManager) or PermissionManager()
    return await write_file_impl(path, content, permissions)
