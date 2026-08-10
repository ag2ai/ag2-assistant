"""The profile's file surfaces: its own Files space, the granted Folders beside
it, the @-picker's corpus and the preview rail's backlink.

Every route here branches on ``os.path.isabs`` and nothing else: a relative path
is a Files-space path, sandboxed to the workspace the profile owns; an absolute
one is a Folder path, authorized through the one Folder resolver (ADR 0006/0013).
The five helpers above ``build_profile_router`` are that branch written once —
which is why they are module-level rather than closures, and why they travel with
this domain instead of routes/common.py.

Pairs with gateway/schemas/file.py (the response models) and
web/src/schemas/file.ts (their zod twins) — same file name in all three trees.
The Thread-scoped Folder ROOTS live in routes/folder.py, following their zod twin.

Two per-route deviations from the shared error set: ``403`` on every mutation (a
``read``-only Folder refuses the write) and ``413`` on the in-place PUT (the body
cap). Both are codes these handlers actually return, and neither is install-wide
enough to belong in ERROR_RESPONSES.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from assistant.filesearch import list_folder_dir, search_corpus
from assistant.folders import READ_WRITE
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import scope_task_id
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    ErrorBody,
    FilesResponse,
    MentionsResponse,
    MkdirResultResponse,
    Ok,
    SearchResultsResponse,
    UploadResultResponse,
    WriteResultResponse,
)
from assistant.workspace import (
    _MAX_WRITE_BYTES,
    delete,
    etag_for_path,
    list_all_dirs,
    list_files,
    make_dir,
    mention_forms,
    move,
    resolve,
    save_upload,
    write_text,
)

# A `read`-only Folder refuses every mutation below with this body. The
# ``description`` is spelled out for the reason ERROR_RESPONSES gives: the
# interpreter's reason-phrase table is version-dependent, the artifact is not.
DENIED_RESPONSE = {403: {"model": ErrorBody, "description": "Forbidden"}}
# ...and the in-place write additionally caps the body it will buffer.
TOO_LARGE_RESPONSE = {413: {"model": ErrorBody, "description": "Content Too Large"}}


class MkdirRequest(BaseModel):
    """Create an empty Directory (ADR 0007). `path` is workspace-relative for the Files
    space; for a Folder (ADR 0006, ticket 05) it is ABSOLUTE and stays inside a granted
    Folder's subtree. `chat_id` scopes the read_write Grant resolution for a Folder
    mkdir (ignored for a relative path)."""

    path: str
    chat_id: str = ""


class MoveRequest(BaseModel):
    """Move/rename a file or Directory (ADR 0007). `from`/`to` are workspace-relative
    for a Files-space move; for a Folder move (ADR 0006, ticket 04) both are ABSOLUTE
    and must resolve under the SAME readable Folder root (no cross-Root move).
    `chat_id` scopes the Grant resolution for a Folder move (ignored for a relative
    move — the Files space is profile-sandboxed)."""

    from_: str = Field(alias="from")
    to: str
    chat_id: str = ""

    model_config = {"populate_by_name": True}


def _unquote_etag(value: str | None) -> str | None:
    """The raw content token inside an ``If-Match`` header value — its weak-``W/``
    prefix and surrounding quotes stripped (ADR 0011), or None when absent."""
    if value is None:
        return None
    value = value.strip()
    if value.startswith("W/"):
        value = value[2:]
    return value.strip('"')


def _resolve_folder(
    runtime: ProfileRuntime, path: str, chat_id: str, task_id: str = ""
) -> tuple[Path | None, str | None]:
    """``(readable Folder root containing ``path``, its effective mode)`` via the one
    Folder resolver, or ``(None, None)`` when there's no gateway or the absolute
    ``path`` resolves under no granted root. Read authorizes on any non-``None`` mode;
    a mutation additionally requires ``read_write`` (the caller checks). ``task_id``
    (decoded from the Thread scope) admits the open Task/run's task-scope Grants. The
    confining root is the sandbox base every absolute ``/files/*`` mutation passes to
    the workspace helpers, so ``within-subtree only`` falls out for free (ADR 0006/0013)."""
    gw = runtime.gateway
    folders = gw.folders if gw is not None else None
    if folders is None:
        return None, None
    return folders.resolve_within(path, runtime.config.data_dir.name, chat_id, task_id)


def _folder_write_base(
    runtime: ProfileRuntime,
    path: str,
    chat_id: str,
    *,
    task_id: str = "",
    miss_status: int,
    miss_msg: str,
) -> tuple[Path | None, JSONResponse | None]:
    """The sandbox base for an ABSOLUTE ``/files/*`` MUTATION (tickets 04–05): the
    confining readable Folder root when ``path`` resolves under a ``read_write`` Grant,
    else ``(None, <deny response>)`` — the caller's ``miss_status``/``miss_msg`` (e.g.
    404 "file not found", 400 "invalid path") when the path is under no granted root,
    always ``403`` "read-only folder" when the covering Grant is ``read``. Every
    absolute mutation branch funnels through here so the read_write gate + base
    selection is written once (ADR 0006)."""
    root, mode = _resolve_folder(runtime, path, chat_id, task_id)
    if root is None:
        return None, JSONResponse({"error": miss_msg}, status_code=miss_status)
    if mode != READ_WRITE:
        return None, JSONResponse({"error": "read-only folder"}, status_code=403)
    return root, None


def _mutation_base(
    runtime: ProfileRuntime,
    path: str,
    chat_id: str,
    *,
    task_id: str = "",
    miss_status: int,
    miss_msg: str,
) -> tuple[Path | None, JSONResponse | None]:
    """The sandbox base for a ``/files/*`` mutation, branching on ``os.path.isabs``: the
    workspace for a relative path, else the ``read_write`` Folder root (or a deny response)."""
    if os.path.isabs(path):
        return _folder_write_base(
            runtime, path, chat_id, task_id=task_id, miss_status=miss_status, miss_msg=miss_msg
        )
    return runtime.config.workspace_dir, None


def _resolve_file_path(
    runtime: ProfileRuntime, path: str, chat_id: str, task_id: str = ""
) -> tuple[Path | None, str | None]:
    """Resolve a ``/files/*`` ``path`` to ``(existing file, effective mode)``, branching
    on ``os.path.isabs`` — the sole discriminator between a Files-space file and a
    Folder file. A relative path keeps today's workspace sandbox untouched (mode
    ``read_write`` — the user owns their Files space); an absolute path authorizes via
    the one Folder resolver (``read`` suffices for a GET, ``task_id`` admitting the open
    Task/run's grants) and is confirmed to be a real file. ``(None, None)`` on any
    denial/miss — the caller turns that into the shared 404 shape (ADR 0006/0013). The
    mode rides back so the GET can advertise it to the client's edit-affordance gating
    (ticket 04)."""
    if not os.path.isabs(path):
        rp = resolve(runtime.config.workspace_dir, path)
        return (rp, READ_WRITE) if rp is not None else (None, None)
    root, mode = _resolve_folder(runtime, path, chat_id, task_id)
    if root is None:
        return None, None
    rp = Path(path).expanduser().resolve()
    return (rp, mode) if rp.is_file() else (None, None)


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    """The /api/p/{pid} file routes, in the order they had in app.py."""
    r = APIRouter()

    @r.get("/files", response_model=FilesResponse)
    async def list_workspace_files(
        path: str = "", chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """List a file tree, branching on ``os.path.isabs`` (the sole discriminator).

        With no ``path`` (or a relative one) — the profile's whole Files space: files
        plus every Directory (so the tree can show empty Directories the files-only
        listing omits). Shared read+write; agent writes and user uploads land here
        alike (ADR 0007).

        With an ABSOLUTE ``path`` — ONE Directory level inside a granted **Folder** (a
        directory outside the Root), authorized through the one resolver (``read``
        suffices), scoped to the open Thread (``chat_id``, plus the task decoded from it
        so a Task page/run sees its task-scope Folders — ADR 0006/0013), with the usual
        noise Directories pruned. The tree lazy-expands one level per call. A
        denied/missing path is a 404 — the same shape either branch."""
        if path and os.path.isabs(path):
            gw = runtime.gateway
            folders = gw.folders if gw is not None else None
            task_id = await scope_task_id(runtime, chat_id)
            mode = (
                folders.mode_for_path(path, runtime.config.data_dir.name, chat_id, task_id)
                if folders is not None
                else None
            )
            if mode is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            listing = list_folder_dir(path)
            if listing is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            # This level's own resolved mode (not the root's) so the tree derives each
            # nested Directory's write affordances from the Grant that actually covers
            # it — a read_write Folder nested under a read root reads as writable when
            # descended into (ticket 04, "affordances derived from the resolved mode").
            return {**listing, "mode": mode}

        return {
            "root": str(Path(runtime.config.workspace_dir).expanduser()),
            "files": list_files(runtime.config.workspace_dir),
            "dirs": list_all_dirs(runtime.config.workspace_dir),
        }

    @r.get("/files/search", response_model=SearchResultsResponse)
    async def search_workspace_files(
        q: str = "", chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """The ``@``-picker's corpus search: a bounded, ranked list of files matching
        `q` across the profile's Files space **and** every Folder this profile∪task∪chat
        can read, each with an ABSOLUTE `path` the agent's ``read_file`` can open.
        Ranked filename-first; a blank/no-match query yields an empty list, not an
        error. Honors the same ``mode_for`` resolution the agent's reads use, so a
        denied file is never surfaced (ADR 0006/0012)."""
        gw = runtime.gateway
        # The Thread's scope carries its task in the chat_id slot (a run thread's
        # ``task-run:{run_id}``, a Task page's ``task:{id}``) — decode it so the picker
        # sees the task-scoped Folder grants too (the one shared decoder).
        task_id = await scope_task_id(runtime, chat_id)
        return {
            "results": search_corpus(
                runtime.config.workspace_dir,
                q,
                folders=gw.folders if gw is not None else None,
                profile=runtime.config.data_dir.name,
                chat_id=chat_id,
                task_id=task_id,
            )
        }

    @r.get("/files/mentions", response_model=MentionsResponse)
    async def file_mentions(
        path: str = "", chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """The preview rail's "Mentioned in N threads" backlink (ADR 0014): the
        current profile's Threads (Chats + Task Runs) whose transcript mentions the
        previewed file, newest-first. ``path`` is the previewed file's path (relative
        = Files-space, absolute = Folder); its OR-set of forms (absolute + workspace-
        relative) is loose-substring-scanned over each stream's transcript + event
        log. Read-only over this profile's own store — no auth/grant check beyond the
        profile boundary. ``chat_id`` is accepted for signature parity with the other
        ``/files`` routes but not needed (the scan is profile-wide)."""
        gw = runtime.gateway
        forms = mention_forms(runtime.config.workspace_dir, path)
        if gw is None or not forms:
            return {"threads": []}
        return {"threads": await gw.threads_mentioning(forms)}

    @r.post("/files/upload", response_model=UploadResultResponse, responses=DENIED_RESPONSE)
    async def upload_workspace_files(
        files: list[UploadFile] = File(...),
        dir: str = Form(""),
        chat_id: str = Form(""),
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Upload one or more files into `dir` (root when empty), auto-suffixing name
        clashes so nothing is overwritten (ADR 0007). A RELATIVE `dir` targets the
        Files-space sandbox (unchanged). An ABSOLUTE `dir` targets a Folder Directory:
        it authorizes through the one resolver requiring `read_write` (a `read`-only
        Folder is `403`), scoped to the open Thread (`chat_id` + the task decoded from
        it), with the upload confined to that Folder's subtree (ticket 05). A `dir` that
        escapes its root is rejected `400`."""
        task_id = await scope_task_id(runtime, chat_id)
        base, deny = _mutation_base(
            runtime,
            dir,
            chat_id,
            task_id=task_id,
            miss_status=400,
            miss_msg="invalid target directory",
        )
        if deny is not None:
            return deny

        saved: list[str] = []
        for f in files:
            data = await f.read()
            rel = save_upload(base, f.filename or "file", data, dir)
            if rel is None:
                return JSONResponse({"error": "invalid target directory"}, status_code=400)
            saved.append(rel)
        return {"ok": True, "saved": saved}

    @r.post("/files/mkdir", response_model=MkdirResultResponse, responses=DENIED_RESPONSE)
    async def mkdir_workspace(req: MkdirRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Create an empty Directory. 409 if it already exists (no clobber), 400 on a
        traversal escape / empty path. A RELATIVE `path` lands in the Files-space
        sandbox (unchanged). An ABSOLUTE `path` creates a Folder Directory: it
        authorizes through the one resolver requiring `read_write` (a `read`-only Folder
        is `403`), scoped to the open Thread (`chat_id` + the task decoded from it),
        confined to that Folder's subtree (ticket 05)."""
        task_id = await scope_task_id(runtime, req.chat_id)
        base, deny = _mutation_base(
            runtime,
            req.path,
            req.chat_id,
            task_id=task_id,
            miss_status=400,
            miss_msg="invalid path",
        )
        if deny is not None:
            return deny

        status, rel = make_dir(base, req.path)
        if status == "ok":
            return {"ok": True, "path": rel}
        code, msg = (409, "directory exists") if status == "exists" else (400, "invalid path")
        return JSONResponse({"error": msg}, status_code=code)

    @r.post("/files/move", response_model=Ok, responses=DENIED_RESPONSE)
    async def move_workspace(req: MoveRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Move/rename a file or Directory. 409 if the destination already exists
        (never overwrites, ADR 0007), 404 if the source is missing, 400 on a
        traversal escape.

        A RELATIVE ``from`` moves within the Files-space sandbox (unchanged). An
        ABSOLUTE ``from`` is a Folder move: it authorizes through the one resolver
        requiring ``read_write`` (a ``read``-only Folder is ``403``), scoped to
        ``chat_id``, and is confined to the source's own readable Folder root — so a
        ``to`` that resolves outside it (another root, the Files space, or a relative
        target) is rejected ``400`` (no cross-Root move, ticket 04)."""
        # Cross-space/cross-Root guard: an absolute (Folder) source's target must itself
        # be absolute; move() then confines it under the source's root.
        if os.path.isabs(req.from_) and not os.path.isabs(req.to):
            return JSONResponse({"error": "invalid path"}, status_code=400)
        task_id = await scope_task_id(runtime, req.chat_id)
        base, deny = _mutation_base(
            runtime,
            req.from_,
            req.chat_id,
            task_id=task_id,
            miss_status=404,
            miss_msg="source not found",
        )
        if deny is not None:
            return deny

        outcome = move(base, req.from_, req.to)
        if outcome == "ok":
            return {"ok": True}
        status, msg = {
            "exists": (409, "destination exists"),
            "not_found": (404, "source not found"),
            "invalid": (400, "invalid path"),
        }[outcome]
        return JSONResponse({"error": msg}, status_code=status)

    @r.get("/files/raw", response_model=None)
    async def workspace_file(
        path: str,
        download: bool = False,
        chat_id: str = "",
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Serve one file (view inline or download). A RELATIVE ``path`` is a
        Files-space file, sandboxed to the workspace root (ADR 0007). An ABSOLUTE
        ``path`` is a Folder file (a file inside a granted Folder outside the Root):
        authorized through the one resolver (``read`` suffices), scoped to the open
        Thread's ``chat_id`` (ADR 0006/0013). Either way carries an ``ETag``
        content-version token (ADR 0011) an in-place ``PUT`` echoes back as
        ``If-Match``. A denied/missing path is a 404 — the same shape either branch.
        The response also carries ``X-File-Mode`` (``read``|``read_write``) — the
        effective Grant mode — so the client gates its edit/rename/delete affordances
        off the same server truth a mutation is enforced against (ticket 04).

        ``response_model=None``: the body is the file's bytes, which OpenAPI cannot
        describe as a JSON schema."""
        task_id = await scope_task_id(runtime, chat_id)
        rp, mode = _resolve_file_path(runtime, path, chat_id, task_id)
        if rp is None:
            return JSONResponse({"error": "file not found"}, status_code=404)
        disp = "attachment" if download else "inline"
        # Let FileResponse build Content-Disposition so a non-ASCII filename (user
        # uploads keep their original name) is RFC 5987-encoded, not latin-1 crashed.
        resp = FileResponse(rp, filename=rp.name, content_disposition_type=disp)
        etag = etag_for_path(rp)
        if etag is not None:
            resp.headers["ETag"] = f'"{etag}"'  # RFC 7232 quoted-string
        if mode is not None:
            resp.headers["X-File-Mode"] = mode
        return resp

    @r.put(
        "/files/raw",
        response_model=WriteResultResponse,
        responses={**DENIED_RESPONSE, **TOO_LARGE_RESPONSE},
    )
    async def write_workspace_file(
        path: str,
        request: Request,
        chat_id: str = "",
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Overwrite an existing file's contents in place from the request body
        (UTF-8), using ``If-Match`` as the base ETag (ADR 0011). ``200`` + the new
        ``ETag`` on success, ``404`` if the path is missing, ``409`` if ``If-Match``
        is stale, ``400`` on a traversal/invalid path; omitting ``If-Match`` forces
        past the compare.

        A RELATIVE ``path`` writes in the Files-space sandbox (unchanged). An ABSOLUTE
        ``path`` is a Folder file: it authorizes through the one resolver requiring
        ``read_write`` (a ``read``-only Folder file is ``403``), scoped to the open
        Thread's ``chat_id``, and the write is confined to that Folder's own subtree
        (ticket 04)."""
        # Resolve the sandbox base BEFORE buffering the body: an unauthorized Folder
        # write is refused without reading its (capped) payload.
        task_id = await scope_task_id(runtime, chat_id)
        base, deny = _mutation_base(
            runtime, path, chat_id, task_id=task_id, miss_status=404, miss_msg="file not found"
        )
        if deny is not None:
            return deny

        # Stream the body under a hard cap so an oversize (or lying Content-Length) PUT
        # can't buffer unboundedly into memory before the size check (DoS guard).
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > _MAX_WRITE_BYTES:
                return JSONResponse({"error": "file too large"}, status_code=413)
            chunks.append(chunk)
        try:
            content = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError:
            return JSONResponse({"error": "body must be valid UTF-8"}, status_code=400)
        if_match = request.headers.get("if-match")
        base_token = _unquote_etag(if_match)  # strip weak prefix + quotes to the raw token
        status, new_tag = write_text(
            base,
            path,
            content,
            base_token=base_token,
            force=if_match is None,  # no If-Match ⇒ forced overwrite (bypass compare)
        )
        if status == "ok":
            headers = {"ETag": f'"{new_tag}"'}
            return JSONResponse({"ok": True, "etag": new_tag}, headers=headers)
        code, msg = {
            "not_found": (404, "file not found"),
            "conflict": (409, "file changed on disk"),
            "invalid": (400, "invalid path"),
            "too_large": (413, "file too large"),
        }[status]
        return JSONResponse({"error": msg}, status_code=code)

    @r.delete("/files/raw", response_model=Ok, responses=DENIED_RESPONSE)
    async def delete_workspace_file(
        path: str, chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Delete a file — or a Directory and everything in it, recursively (ADR
        0007). A RELATIVE ``path`` is sandboxed to the Files-space root (unchanged). An
        ABSOLUTE ``path`` is a Folder file/Directory: it authorizes through the one
        resolver requiring ``read_write`` (a ``read``-only Folder is ``403``), scoped
        to ``chat_id``, and the delete is confined to that Folder's own subtree
        (emptied parents pruned up to — never including — the Folder root, ticket 04)."""
        task_id = await scope_task_id(runtime, chat_id)
        base, deny = _mutation_base(
            runtime, path, chat_id, task_id=task_id, miss_status=404, miss_msg="file not found"
        )
        if deny is not None:
            return deny

        if not delete(base, path):
            return JSONResponse({"error": "file not found"}, status_code=404)
        return {"ok": True}

    return r
