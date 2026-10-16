"""Permission-gated file reads, block edits, writes and folder listings."""

from collections.abc import Iterator
from pathlib import Path

from ag2 import Context, DocumentInput, ImageInput, ToolResult, tool

from assistant.permissions import PermissionManager
from assistant.tools.file_edits import FileEdit, apply_edits, edit_details, normalize_newlines

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MAX_TEXT_BYTES = 10 * 1024
_MAX_TEXT_LINES = 500


def _file_target(path: str, permissions: PermissionManager) -> Path:
    """Resolve relative paths against the configured workspace."""
    raw = path.strip()
    if not raw:
        raise ValueError("A file path is required.")
    target = Path(raw).expanduser()
    if not target.is_absolute():
        if permissions.workspace_dir is None:
            raise ValueError(
                "No workspace is available for a relative path — use an absolute path."
            )
        target = (permissions.workspace_dir / target).resolve()
    return target


def _text_lines(target: Path) -> Iterator[tuple[str | None, int, int, bool]]:
    """Yield LF-delimited lines and counts, retaining at most 10 KiB of a line."""
    with target.open(encoding="utf-8", errors="replace", newline="\n") as source:
        parts: list[str] = []
        size = characters = 0
        while block := source.readline(_MAX_TEXT_BYTES):
            terminated = block.endswith("\n")
            part = block[:-1] if terminated else block
            size += len(part.encode("utf-8"))
            characters += len(part)
            if size <= _MAX_TEXT_BYTES:
                parts.append(part)
            else:
                parts.clear()
            if terminated:
                yield "".join(parts) if size <= _MAX_TEXT_BYTES else None, size, characters, False
                parts, size, characters = [], 0, 0
        yield "".join(parts) if size <= _MAX_TEXT_BYTES else None, size, characters, True


def _read_text_chunk(target: Path, offset: int, limit: int | None) -> str:
    """Select complete lines under both caps and count the rest without retaining it."""
    selected: list[str] = []
    total_lines = total_characters = returned_bytes = 0
    stopped = oversized = False
    for line, size, characters, final in _text_lines(target):
        total_lines += 1
        total_characters += characters + int(not final)
        if total_lines < offset or stopped:
            continue
        terminal_empty = final and size == 0 and bool(selected)
        if (
            (len(selected) >= _MAX_TEXT_LINES and not terminal_empty)
            or (limit is not None and len(selected) >= limit)
            or returned_bytes + size + int(bool(selected)) > _MAX_TEXT_BYTES
        ):
            stopped = True
            oversized = not selected and size > _MAX_TEXT_BYTES
            continue
        assert line is not None
        returned_bytes += size + int(bool(selected))
        selected.append(line)
    if offset > total_lines:
        return f"Offset {offset} is beyond the end of the file ({total_lines} lines)."
    text = "\n".join(selected)
    next_offset = offset + len(selected)
    remaining = total_lines - next_offset + 1
    footer = (
        f"\n\n[read_file]\nFile size: {target.stat().st_size} bytes; "
        f"{total_characters} characters; {total_lines} lines.\n"
        f"Returned: {len(selected)} lines, {returned_bytes} bytes, "
        f"{len(text)} characters (offset {offset}).\n"
        f"Remaining after this chunk: {remaining} lines.\n"
    )
    if oversized:
        footer += (
            f"Line {offset} exceeds the 10 KiB limit. Use the shell to read a bounded portion "
            "of this line; line-based pagination cannot split it."
        )
    elif remaining:
        limit_argument = f", limit={limit}" if limit is not None else ""
        footer += (
            "Content truncated. Continue reading as needed with "
            f"read_file(path={str(target)!r}, offset={next_offset}{limit_argument})."
        )
    else:
        footer += "End of file."
    return f"Contents of {target.name}:\n\n{text}{footer}"


async def read_file_impl(
    path: str,
    permissions: PermissionManager,
    *,
    offset: int = 1,
    limit: int | None = None,
) -> "ToolResult | str":
    """Permission-gated file read: paginated text or vision content for documents/images."""
    if offset < 1 or (limit is not None and limit <= 0):
        return "read_file needs an offset of at least 1 and a positive limit (both in lines)."
    try:
        target = _file_target(path, permissions)
    except ValueError as exc:
        return str(exc)
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
    if suffix not in _IMAGE_SUFFIXES and suffix != ".pdf":
        try:
            return _read_text_chunk(target, offset, limit)
        except OSError as exc:
            return f"Could not read {path}: {exc}"

    if offset != 1 or limit is not None:
        return "offset and limit apply only to text files. Read this file without pagination arguments."
    if suffix in _IMAGE_SUFFIXES:
        return ToolResult(ImageInput(path=str(target)))

    # PDFs are sent for vision reading.
    return ToolResult(DocumentInput(path=str(target)))


@tool
async def read_file(
    path: str, context: Context, offset: int = 1, limit: int | None = None
) -> "ToolResult | str":
    """Read with folder permission: text chunks include size, returned/remaining counts and
    the next offset to continue as needed; documents/images are read visually in full.

    Args:
        path: Workspace-relative, absolute, or ~-relative file path.
        offset: First line to read, starting at 1; use the footer's next offset.
        limit: Maximum lines to read; default/cap 500 lines or 10 KiB, whichever comes first.
    """
    permissions = context.dependencies.get(PermissionManager) or PermissionManager()
    return await read_file_impl(path, permissions, offset=offset, limit=limit)


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
        (e.name + "/") if e.is_dir() else e.name for e in entries if not e.name.startswith(".")
    ]
    if not names:
        return f"{target} is empty."
    shown = names[:_MAX_LIST_ENTRIES]
    suffix = (
        f"\n… ({len(names) - len(shown)} more entries not shown)" if len(names) > len(shown) else ""
    )
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


async def write_file_impl(
    path: str, content: str, permissions: PermissionManager, *, overwrite: bool = False
) -> str:
    """Create a UTF-8 file, warning before replacing an existing file."""
    try:
        p = _file_target(path, permissions)
    except ValueError as exc:
        return str(exc)
    if not await permissions.check(p, write=True):
        return (
            f"The user denied write permission for {p.parent}. Do not try to write "
            f"{p.name} another way (e.g. shell or code) — tell the user you don't "
            "have write permission there."
        )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w" if overwrite else "x", encoding="utf-8", newline="") as destination:
            destination.write(content)
    except FileExistsError:
        return (
            f"Warning: {p} already exists. Nothing was written. Use update_file for block edits "
            "or repeat write_file with overwrite=true to replace the entire file."
        )
    except OSError as exc:
        return f"Could not write {path}: {exc}"
    return f"Wrote {len(content.encode('utf-8'))} bytes to {p}."


@tool
async def write_file(path: str, content: str, context: Context, overwrite: bool = False) -> str:
    """Write UTF-8 text, creating parent folders. Existing files trigger a warning
    without writing; explicitly repeat with overwrite=true to replace them.

    Args:
        path: Workspace-relative, absolute, or ~-relative file path.
        content: The full text content to write.
        overwrite: Explicitly replace an existing file after the overwrite warning.
    """
    permissions = context.dependencies.get(PermissionManager) or PermissionManager()
    return await write_file_impl(path, content, permissions, overwrite=overwrite)


async def update_file_impl(
    path: str, edits: list[FileEdit], permissions: PermissionManager
) -> ToolResult | str:
    """Apply unique, non-overlapping replacements after checking folder write permission."""
    try:
        target = _file_target(path, permissions)
    except ValueError as exc:
        return str(exc)
    if not await permissions.check(target, write=True):
        return (
            f"The user denied write permission for {target.parent}. Do not try to modify "
            f"{target.name} another way — tell the user you don't have write permission there."
        )
    try:
        raw = target.read_bytes().decode("utf-8", errors="replace")
        bom = "\ufeff" if raw.startswith("\ufeff") else ""
        content = raw[len(bom) :]
        first_lf = content.find("\n")
        ending = "\r\n" if first_lf > 0 and content[first_lf - 1] == "\r" else "\n"
        original = normalize_newlines(content)
        changed = apply_edits(original, edits)
        details = edit_details(path, original, changed)
        result = bom + (changed.replace("\n", "\r\n") if ending == "\r\n" else changed)
        target.write_bytes(result.encode("utf-8"))
    except (OSError, ValueError) as exc:
        return f"Could not update {path}: {exc}"
    return ToolResult(
        f"Successfully replaced {len(edits)} block(s) in {target}.\n{details['diff']}",
        metadata=details,
    )


@tool
async def update_file(path: str, edits: list[FileEdit], context: Context) -> ToolResult | str:
    """Replace unique text blocks in an existing file, checking all edits before writing.
    Matching tolerates trailing spaces and Unicode typography; preserves untouched lines.

    Args:
        path: Workspace-relative, absolute, or ~-relative file path; needs write permission.
        edits: Blocks with oldText and newText; match the original file and must not overlap.
    """
    permissions = context.dependencies.get(PermissionManager) or PermissionManager()
    return await update_file_impl(path, edits, permissions)
