"""The profile's Files space, the granted-Folder listings beside it, the
@-picker's corpus and the preview rail's backlink. Mirrors web/src/schemas/file.ts.

``GET /api/p/{pid}/files`` answers two different shapes off one path — the
discriminator is whether the requested path is absolute, not a field in the body —
so its model is the union of both, and the branches are told apart by their
required keys (``root`` against ``path``/``mode``).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class FileRowOut(BaseModel):
    """One file in the Files space, workspace-relative (workspace.py list_files).
    ``dir`` is the empty string at the root, not ``"."``."""

    path: str
    name: str
    dir: str
    size: int
    modified: str


class FilesListingResponse(BaseModel):
    """The whole Files space. ``dirs`` is a flat list of workspace-relative
    directory paths, listed separately from the files so the tree can show the
    empty Directories a files-only listing omits."""

    root: str
    files: list[FileRowOut]
    dirs: list[str]


class FolderDirOut(BaseModel):
    """One subdirectory inside a granted Folder — absolute ``path``, because a
    Folder lives outside the Root and has no relative form to be resolved against."""

    name: str
    path: str


class FolderFileOut(BaseModel):
    """One file inside a granted Folder. No ``modified``: the Folder listing is a
    lazy one-level expand, and the tree shows neither a date nor a sort by it."""

    name: str
    path: str
    size: int


class FolderListingResponse(BaseModel):
    """ONE Directory level inside a granted Folder. ``mode`` is THIS level's
    resolved Grant mode rather than its root's, so the tree derives each nested
    Directory's write affordances from the Grant that actually covers it."""

    path: str
    dirs: list[FolderDirOut]
    files: list[FolderFileOut]
    mode: Literal["read", "read_write"]


# GET /api/p/{pid}/files. Branch order matches web/src/schemas/file.ts, which the
# zod gate compares against by index.
FilesResponse = FilesListingResponse | FolderListingResponse


class SearchHitOut(BaseModel):
    """One @-picker hit (filesearch.py _candidate). ``path`` is ABSOLUTE either way
    — the agent's read_file opens it directly — and ``dir`` is a display label
    rooted at the Folder's basename, not a resolvable path."""

    path: str
    name: str
    dir: str
    kind: Literal["file", "directory"]


class SearchResultsResponse(BaseModel):
    """GET /api/p/{pid}/files/search — bounded and ranked; a blank or no-match
    query is an empty list, not an error."""

    results: list[SearchHitOut]


class MentionChatOut(BaseModel):
    """A plain Chat thread that mentions the previewed file."""

    stream_id: str
    kind: Literal["chat"]
    title: str
    updated: str


class MentionRunOut(BaseModel):
    """A Task Run thread that mentions it, enriched with its parent Task so the
    popover row can say which Task the run belongs to."""

    stream_id: str
    kind: Literal["run"]
    title: str
    updated: str
    task_id: str
    task_name: str
    run_started_at: str


# Declared discriminated rather than left to the smart union the two Literals
# would already resolve: the branch is a documented fact about the wire, and saying
# so emits an OpenAPI ``discriminator`` a generated client can switch on instead of
# trying each member. Mirrors zod's discriminatedUnion in file.ts.
MentionRowOut = Annotated[MentionChatOut | MentionRunOut, Field(discriminator="kind")]


class MentionsResponse(BaseModel):
    """GET /api/p/{pid}/files/mentions — the "Mentioned in N threads" backlink
    (ADR 0014), newest-first."""

    threads: list[MentionRowOut]


class UploadResultResponse(BaseModel):
    """POST /api/p/{pid}/files/upload — the saved names, which are the names the
    client sent only when nothing clashed: an upload auto-suffixes rather than
    overwrite (ADR 0007), so the client has to read them back from here."""

    ok: Literal[True]
    saved: list[str]


class MkdirResultResponse(BaseModel):
    """POST /api/p/{pid}/files/mkdir."""

    ok: Literal[True]
    path: str


class WriteResultResponse(BaseModel):
    """PUT /api/p/{pid}/files/raw — the new content token an in-place write
    produces (ADR 0011). It rides the ETag header too; this is the body form, and
    the client prefers it because a proxy may strip the header."""

    ok: Literal[True]
    etag: str
