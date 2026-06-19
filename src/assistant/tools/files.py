"""Local file reading — permission-gated, vision-first.

`read_file` lets the agent read a local file by path. The first access to a
folder requires the user's permission (Claude-Code-style). PDFs and images are
returned to the model as multimodal content so it reads them by *vision* (works
on scanned PDFs with no text layer); text files are returned as text.
"""

from pathlib import Path

from autogen.beta import Context, DocumentInput, ImageInput, ToolResult, tool

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
            f"File not found: {path}. Do not try to locate it by running shell "
            "commands or code. Tell the user it wasn't found and ask how they'd "
            "like to proceed (e.g. confirm the path)."
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
