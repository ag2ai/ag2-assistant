"""Permission-gated host file tools and Pi-style reads, block edits and writes."""

import json
import re

import pytest
from ag2 import ToolResult
from ag2.context import ConversationContext
from ag2.events import ToolCallEvent, ToolErrorEvent
from ag2.stream import MemoryStream

from assistant.folders import READ, READ_WRITE, FolderStore
from assistant.permissions import PermissionManager, PermissionStore
from assistant.tools import build_agent_tools
from assistant.tools.file_edits import FileEdit
from assistant.tools.files import (
    list_folder_impl,
    read_file,
    read_file_impl,
    update_file,
    update_file_impl,
    write_file,
    write_file_impl,
)


def _manager(tmp_path, workspace=None):
    return PermissionManager(
        PermissionStore(path=tmp_path / "perm.json"),
        asker=None,
        folders=FolderStore(path=tmp_path / "folders.json"),
        profile="p1",
        workspace_dir=workspace,
    )


def _text_response(out):
    assert isinstance(out, str)
    body, footer = out.rsplit("\n\n[read_file]\n", 1)
    return body.split("\n\n", 1)[1], footer


def _edit(old, new):
    return FileEdit(oldText=old, newText=new)


async def test_read_file_small_text_reports_size_and_eof_without_writing(tmp_path):
    target = tmp_path / "note.txt"
    text = "Привет 🌍\r\nsecond line"
    raw = text.encode("utf-8")
    target.write_bytes(raw)
    chunk, footer = _text_response(
        await read_file_impl(str(target), _manager(tmp_path, workspace=tmp_path))
    )
    assert chunk == text
    assert f"File size: {len(raw)} bytes; {len(text)} characters; 2 lines." in footer
    assert f"Returned: 2 lines, {len(raw)} bytes, {len(text)} characters (offset 1)." in footer
    assert "Remaining after this chunk: 0 lines." in footer
    assert footer.endswith("End of file.")
    assert "read_file(path=" not in footer
    assert target.read_bytes() == raw


@pytest.mark.parametrize("limit", [None, 500, 100_000])
async def test_read_file_stops_at_500_complete_lines(tmp_path, limit):
    target = tmp_path / "many.txt"
    lines = [f"line {i}" for i in range(505)]
    target.write_text("\n".join(lines))
    chunk, footer = _text_response(
        await read_file_impl(str(target), _manager(tmp_path, workspace=tmp_path), limit=limit)
    )
    assert chunk == "\n".join(lines[:500])
    assert "Returned: 500 lines," in footer
    assert "Remaining after this chunk: 5 lines." in footer
    assert "offset=501" in footer


async def test_read_file_byte_cap_counts_utf8_and_keeps_complete_lines(tmp_path):
    target = tmp_path / "data.txt"
    line = "я🌍" * 100
    target.write_text("\n".join([line] * 20))
    chunk, footer = _text_response(
        await read_file_impl(str(target), _manager(tmp_path, workspace=tmp_path), limit=100_000)
    )
    assert chunk == "\n".join([line] * 17)
    assert len(chunk.encode("utf-8")) == 10_216
    assert "Returned: 17 lines, 10216 bytes," in footer
    assert "Remaining after this chunk: 3 lines." in footer
    assert "offset=18" in footer


@pytest.mark.parametrize("characters", ["x" * 10_241, "я" * 5_121, "🌍" * 50_000])
async def test_read_file_oversized_first_line_reports_shell_fallback(tmp_path, characters):
    target = tmp_path / "long.txt"
    target.write_text(characters + "\nlast")
    manager = _manager(tmp_path, workspace=tmp_path)
    chunk, footer = _text_response(await read_file_impl(str(target), manager))
    assert chunk == ""
    assert "Returned: 0 lines, 0 bytes," in footer
    assert "Line 1 exceeds the 10 KiB limit" in footer
    assert "shell" in footer and "read_file(path=" not in footer
    assert _text_response(await read_file_impl(str(target), manager, offset=2))[0] == "last"


async def test_read_file_line_at_exact_byte_limit_fits(tmp_path):
    target = tmp_path / "boundary.txt"
    target.write_text("x" * 10_240 + "\nlast")
    chunk, footer = _text_response(
        await read_file_impl(str(target), _manager(tmp_path, workspace=tmp_path))
    )
    assert chunk == "x" * 10_240
    assert "offset=2" in footer


@pytest.mark.parametrize("text", ["", "hello\n", "short\n" * 500, "first\r\nlast\r\n"])
@pytest.mark.parametrize("limit", [None, 100_000])
async def test_read_file_preserves_trailing_newline_when_entire_file_fits(tmp_path, text, limit):
    target = tmp_path / "data.txt"
    target.write_bytes(text.encode("utf-8"))
    chunk, footer = _text_response(
        await read_file_impl(str(target), _manager(tmp_path, workspace=tmp_path), limit=limit)
    )
    assert chunk == text
    assert footer.endswith("End of file.")


@pytest.mark.parametrize(
    "text,limit",
    [
        ("\n".join(f"row {i}" for i in range(1005)), None),
        ("\n".join(["я🌍" * 200] * 31) + "\n", None),
        ("first\r\nвторая\rlast\n" * 20, 7),
    ],
)
async def test_read_file_following_footer_reconstructs_all_text(tmp_path, text, limit):
    target = tmp_path / "data.txt"
    raw = text.encode("utf-8")
    target.write_bytes(raw)
    manager = _manager(tmp_path, workspace=tmp_path)
    chunks = []
    offset = 1
    while True:
        chunk, footer = _text_response(
            await read_file_impl(str(target), manager, offset=offset, limit=limit)
        )
        chunks.append(chunk)
        if footer.endswith("End of file."):
            break
        continuation = re.search(r"offset=(\d+)", footer)
        assert continuation is not None
        next_offset = int(continuation[1])
        assert next_offset > offset
        offset = next_offset
    assert "\n".join(chunks) == text
    assert target.read_bytes() == raw


async def test_read_file_offset_and_limit_select_lines(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("\n".join(f"line {i}" for i in range(600)))
    chunk, footer = _text_response(
        await read_file_impl(
            str(target), _manager(tmp_path, workspace=tmp_path), offset=499, limit=3
        )
    )
    assert chunk == "line 498\nline 499\nline 500"
    assert "Remaining after this chunk: 99 lines." in footer
    assert "offset=502, limit=3" in footer


async def test_read_file_invalid_utf8_and_unknown_extension_are_text(tmp_path):
    target = tmp_path / "source.rs"
    target.write_bytes(b"a\xff\r\nb")
    chunk, footer = _text_response(
        await read_file_impl(str(target), _manager(tmp_path, workspace=tmp_path), limit=1)
    )
    assert chunk == "a�\r"
    assert "File size: 5 bytes; 5 characters; 2 lines." in footer
    assert "offset=2, limit=1" in footer


async def test_read_file_relative_path_uses_workspace(tmp_path):
    target = tmp_path / "data.py"
    target.write_text("print('hello')")
    out = await read_file_impl("data.py", _manager(tmp_path, workspace=tmp_path))
    assert _text_response(out)[0] == "print('hello')"


async def test_read_file_offset_beyond_eof_returns_error(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("one\ntwo")
    out = await read_file_impl(str(target), _manager(tmp_path, workspace=tmp_path), offset=3)
    assert "beyond the end of the file (2 lines)" in out
    assert "read_file(path=" not in out


@pytest.mark.parametrize("offset,limit", [(-1, 10), (0, 10), (1, 0), (1, -1)])
async def test_read_file_rejects_invalid_pagination(tmp_path, offset, limit):
    target = tmp_path / "data.txt"
    target.write_text("secret")
    out = await read_file_impl(
        str(target), _manager(tmp_path, workspace=tmp_path), offset=offset, limit=limit
    )
    assert "offset of at least 1 and a positive limit" in out
    assert "secret" not in out


async def test_read_file_continuation_rechecks_folder_permission(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("first\nsecret")
    manager = _manager(tmp_path)
    folder = manager.folders.grant_path(tmp_path, READ, "p1")
    assert _text_response(await read_file_impl(str(target), manager, limit=1))[0] == "first"
    manager.folders.revoke_grant(folder["id"], profile="p1")
    out = await read_file_impl(str(target), manager, offset=2, limit=1)
    assert "denied permission" in out
    assert "secret" not in out and "File size:" not in out


@pytest.mark.parametrize("suffix", [".pdf", ".png"])
async def test_read_file_vision_content_rejects_pagination(tmp_path, suffix):
    target = tmp_path / f"data{suffix}"
    target.write_bytes(b"content")
    manager = _manager(tmp_path, workspace=tmp_path)
    assert isinstance(await read_file_impl(str(target), manager), ToolResult)
    out = await read_file_impl(str(target), manager, limit=1)
    assert "offset and limit apply only to text files" in out


@pytest.mark.parametrize(
    "arguments,expected", [({}, "one\ntwo\nthree"), ({"offset": 2, "limit": 1}, "two")]
)
async def test_read_file_tool_accepts_line_pagination(tmp_path, arguments, expected):
    target = tmp_path / "note.txt"
    target.write_text("one\ntwo\nthree")
    context = ConversationContext(
        stream=MemoryStream(),
        dependencies={PermissionManager: _manager(tmp_path, workspace=tmp_path)},
    )
    result = await read_file(
        ToolCallEvent(name="read_file", arguments=json.dumps({"path": str(target), **arguments})),
        context,
    )
    assert not isinstance(result, ToolErrorEvent), result
    assert _text_response(result.result.parts[0].content)[0] == expected
    schema = read_file.schema.function.parameters
    assert schema["required"] == ["path"]
    assert schema["properties"]["offset"]["default"] == 1
    assert "500 lines or 10 KiB" in read_file.schema.function.description


async def test_list_folder_requires_read_grant(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    (d / "b.txt").write_text("x")
    (d / "sub").mkdir()
    m = _manager(tmp_path)
    assert "don't have permission" in await list_folder_impl(str(d), m)
    FolderStore(path=tmp_path / "folders.json").grant_path(d, READ, "p1")
    out = await list_folder_impl(str(d), m)
    assert "sub/" in out and "b.txt" in out


async def test_write_file_requires_write_grant(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    m = _manager(tmp_path)
    store = FolderStore(path=tmp_path / "folders.json")
    store.grant_path(d, READ, "p1")
    out = await write_file_impl(str(d / "f.txt"), "hello", m)
    assert "write permission" in out and not (d / "f.txt").exists()
    store.grant_path(d, READ_WRITE, "p1")
    await write_file_impl(str(d / "f.txt"), "hello", m)
    assert (d / "f.txt").read_text() == "hello"


async def test_write_file_relative_lands_in_workspace(tmp_path):
    m = _manager(tmp_path, workspace=tmp_path)
    out = await write_file_impl("notes/a.md", "Привет\r\n", m)
    assert (tmp_path / "notes" / "a.md").read_bytes() == "Привет\r\n".encode("utf-8")
    assert "Wrote 14 bytes" in out


async def test_write_file_relative_escape_needs_grant(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "outside").mkdir()
    m = _manager(tmp_path, workspace=ws)
    out = await write_file_impl("../outside/x.txt", "no", m)
    assert "write permission" in out and not (tmp_path / "outside" / "x.txt").exists()


async def test_write_file_warns_before_overwriting_and_explicit_retry_replaces(tmp_path):
    target = tmp_path / "note.txt"
    target.write_bytes(b"original\r\n")
    before = target.stat()
    manager = _manager(tmp_path, workspace=tmp_path)
    warning = await write_file_impl(str(target), "changed", manager)
    assert "Warning:" in warning and "overwrite=true" in warning
    assert target.read_bytes() == b"original\r\n"
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    result = await write_file_impl(str(target), "changed", manager, overwrite=True)
    assert result.startswith("Wrote")
    assert target.read_bytes() == b"changed"


async def test_write_file_explicit_overwrite_still_checks_permission(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("secret")
    manager = _manager(tmp_path)
    manager.folders.grant_path(tmp_path, READ, "p1")
    out = await write_file_impl(str(target), "changed", manager, overwrite=True)
    assert "denied write permission" in out
    assert target.read_text() == "secret"


async def test_update_file_matches_all_edits_against_original(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("first\nsecond\nthird")
    result = await update_file_impl(
        str(target),
        [_edit("first", "second"), _edit("second", "first")],
        _manager(tmp_path, workspace=tmp_path),
    )
    assert isinstance(result, ToolResult)
    assert target.read_text() == "second\nfirst\nthird"
    assert "Successfully replaced 2 block(s)" in result.parts[0].content
    assert result.metadata["firstChangedLine"] == 1
    assert "-2 second" in result.metadata["diff"]
    assert "+1 second" in result.metadata["diff"]
    assert "@@" in result.metadata["patch"]


@pytest.mark.parametrize(
    "original,edits,error",
    [
        ("same same", [_edit("same", "new")], "must be unique"),
        ("'same' ‘same’", [_edit("'same'", "new")], "must be unique"),
        ("first second", [_edit("first", "new"), _edit("missing", "x")], "Could not find"),
        ("abcdef", [_edit("abcd", "x"), _edit("cdef", "y")], "overlap"),
        ("abcdef", [_edit("abcdef", "x"), _edit("cd", "y")], "overlap"),
        ("same", [_edit("same", "same")], "No changes made"),
        ("same", [_edit("", "x")], "must not be empty"),
        ("same", [_edit("   ", "x")], "must not be empty"),
        ("same", [], "at least one"),
    ],
)
async def test_update_file_validation_failure_leaves_file_untouched(
    tmp_path, original, edits, error
):
    target = tmp_path / "note.txt"
    target.write_text(original)
    before = target.stat().st_mtime_ns
    result = await update_file_impl(str(target), edits, _manager(tmp_path, workspace=tmp_path))
    assert isinstance(result, str) and error in result
    assert target.read_text() == original
    assert target.stat().st_mtime_ns == before


async def test_update_file_fuzzy_match_preserves_untouched_lines_and_bom_crlf(tmp_path):
    target = tmp_path / "note.txt"
    original = "\ufeffkeep ‘quotes’  \r\nvalue = “Ａ” – 1;  \r\nkeep ‘quotes’  \r\n"
    target.write_bytes(original.encode("utf-8"))
    result = await update_file_impl(
        str(target),
        [_edit('value = "A" - 1;', 'value = "B";')],
        _manager(tmp_path, workspace=tmp_path),
    )
    assert isinstance(result, ToolResult)
    assert target.read_bytes() == (
        '\ufeffkeep ‘quotes’  \r\nvalue = "B";\r\nkeep ‘quotes’  \r\n'.encode("utf-8")
    )
    assert result.metadata["firstChangedLine"] == 2


async def test_update_file_disjoint_fuzzy_matches_on_same_line(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("keep ‘quotes’  \n‘left’ and ‘right’  \nlast  \n")
    result = await update_file_impl(
        str(target),
        [_edit("'right'", "R"), _edit("'left'", "L")],
        _manager(tmp_path, workspace=tmp_path),
    )
    assert isinstance(result, ToolResult)
    assert target.read_text() == "keep ‘quotes’  \nL and R\nlast  \n"


async def test_update_file_exact_match_preserves_trailing_spaces_on_touched_line(tmp_path):
    target = tmp_path / "note.txt"
    target.write_bytes(b"first  \r\nsecond  \r\n")
    result = await update_file_impl(
        str(target),
        [_edit("first  \n", "changed  \n")],
        _manager(tmp_path, workspace=tmp_path),
    )
    assert isinstance(result, ToolResult)
    assert target.read_bytes() == b"changed  \r\nsecond  \r\n"


async def test_update_file_relative_path_and_write_grant(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("old")
    manager = _manager(tmp_path)
    manager.folders.grant_path(tmp_path, READ, "p1")
    out = await update_file_impl(str(target), [_edit("old", "new")], manager)
    assert "denied write permission" in out and target.read_text() == "old"
    manager.folders.grant_path(tmp_path, READ_WRITE, "p1")
    assert isinstance(
        await update_file_impl(str(target), [_edit("old", "new")], manager), ToolResult
    )
    assert target.read_text() == "new"
    manager = _manager(tmp_path, workspace=tmp_path)
    assert isinstance(
        await update_file_impl("note.txt", [_edit("new", "last")], manager), ToolResult
    )
    assert target.read_text() == "last"


async def test_update_file_missing_path_does_not_create_file(tmp_path):
    target = tmp_path / "missing.txt"
    out = await update_file_impl(
        str(target), [_edit("old", "new")], _manager(tmp_path, workspace=tmp_path)
    )
    assert isinstance(out, str) and "Could not update" in out
    assert not target.exists()


async def test_update_file_mixed_exact_and_fuzzy_multiline_blocks(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("keep ‘same’  \n‘first’  \nsecond  \nexact  \nkeep ‘same’  \n")
    result = await update_file_impl(
        str(target),
        [_edit("exact", "changed"), _edit("'first'\nsecond", "replacement\nblock")],
        _manager(tmp_path, workspace=tmp_path),
    )
    assert isinstance(result, ToolResult)
    assert target.read_text() == "keep ‘same’  \nreplacement\nblock\nchanged\nkeep ‘same’  \n"


async def test_update_file_delete_last_block_returns_patch(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("first\nlast")
    result = await update_file_impl(
        str(target), [_edit("last", "")], _manager(tmp_path, workspace=tmp_path)
    )
    assert isinstance(result, ToolResult)
    assert target.read_text() == "first\n"
    assert result.metadata["firstChangedLine"] == 2
    assert "-last\n\\ No newline at end of file\n" in result.metadata["patch"]


async def test_update_file_relative_escape_requires_grant(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("old")
    manager = _manager(tmp_path, workspace=workspace)
    out = await update_file_impl("../outside.txt", [_edit("old", "new")], manager)
    assert "denied write permission" in out
    assert target.read_text() == "old"


async def test_mutation_tools_accept_pi_edit_blocks_and_overwrite_flag(tmp_path):
    context = ConversationContext(
        stream=MemoryStream(),
        dependencies={PermissionManager: _manager(tmp_path, workspace=tmp_path)},
    )
    target = tmp_path / "note.txt"
    target.write_text("old")
    result = await update_file(
        ToolCallEvent(
            name="update_file",
            arguments=json.dumps(
                {
                    "path": "note.txt",
                    "edits": [{"oldText": "old", "newText": "new"}],
                }
            ),
        ),
        context,
    )
    assert not isinstance(result, ToolErrorEvent), result
    assert target.read_text() == "new"
    result = await write_file(
        ToolCallEvent(
            name="write_file", arguments=json.dumps({"path": "note.txt", "content": "last"})
        ),
        context,
    )
    assert "Warning:" in result.result.parts[0].content
    assert target.read_text() == "new"
    result = await write_file(
        ToolCallEvent(
            name="write_file",
            arguments=json.dumps(
                {
                    "path": "note.txt",
                    "content": "last",
                    "overwrite": True,
                }
            ),
        ),
        context,
    )
    assert not isinstance(result, ToolErrorEvent), result
    assert target.read_text() == "last"


def test_toolkit_read_write_update_dropped_for_host_tools(tmp_path):
    tools = build_agent_tools(capabilities=["files"], workspace_dir=str(tmp_path))
    for name in ("read_file", "write_file", "update_file"):
        assert [t.name for t in tools].count(name) == 1
    assert next(t for t in tools if t.name == "update_file") is update_file
    assert {"list_folder", "find_files", "delete_file"} <= {t.name for t in tools}
