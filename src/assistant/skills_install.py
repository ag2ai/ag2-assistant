"""Installing skills from Settings — registry search/install (ADR 0017 t04) and
git/upload discover-and-pick (t05).

The Application → Skills page and the Profiles zone Skills tab both grow an install
control; the **surface carries the target** (Application → the Global layer, Profiles
zone → the active Profile). Each route hands this module the right ``SkillRuntime``
(built over the target skills dir), so the same logic lands a skill in either layer —
there is no separate Global/Profile picker.

Everything here operates on the FULL, unfiltered runtime (install writes to the same
store the plugin reads): the SkillStateStore only decides *availability*, never what is
on disk. Registry work reuses ag2's ``SkillsClient`` + ``SkillsLock`` (the same pieces
``SkillSearchToolkit`` composes) so provenance is recorded exactly as the agent's own
install tool records it. Git/upload sources are copied in as **one-time snapshots** — no
origin/ref is tracked (ADR 0017: "update" = delete + re-install).
"""

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from ag2.tools.skills.runtime.local.loader import parse_frontmatter
from ag2.tools.skills.skill_search.client import SkillsClient
from ag2.tools.skills.skill_search.lock import SkillsLock

# Directories never treated as (or descended into for) skills — VCS/build noise that a
# clone or zip commonly carries. Mirrors the registry extractor's exclude set.
_EXCLUDE_DIRS = frozenset({".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv"})

# URL schemes an install ``git_url`` may use. A transport-helper form (``ext::``,
# ``fd::``) or any other scheme is refused: git's ``ext::`` runs a shell command at
# clone time — on the HOST, before any skill sandbox — so an unvalidated URL is remote
# code execution. A bare local path (no scheme) stays allowed for the tests.
_ALLOWED_GIT_SCHEMES = frozenset({"https", "http", "ssh", "git"})

# Uncompressed-size caps for an uploaded archive: a decompression bomb or a huge upload
# must not exhaust disk/RAM. Per-file mirrors the registry extractor's 25 MB; the total
# bounds the whole expansion.
_MAX_MEMBER_BYTES = 25 * 1024 * 1024  # 25 MB per extracted file (matches the registry)
_MAX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MB total uncompressed across the archive

# Agent Skills names are one safe path component: lowercase alphanumeric words joined
# by single hyphens, capped at 64 characters.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillSourceError(Exception):
    """A git/upload source could not be fetched, unpacked, or validated (ADR 0017 t05).
    Raised with a user-facing message; the HTTP layer turns it into a 4xx."""


# --- registry (skills.sh) -------------------------------------------------------


async def registry_search(query: str, limit: int = 10) -> list[dict]:
    """Search skills.sh and project each hit to the fields the UI needs: an
    ``install_id`` ready to pass back to :func:`registry_install`, plus name and
    description for the checklist row. Reuses ``SkillsClient.search`` — the same call
    the agent's ``search_skills`` tool makes."""
    query = (query or "").strip()
    if not query:
        return []
    records = await SkillsClient().search(query, limit)
    out: list[dict] = []
    for s in records:
        skill_id_val = s.get("skillId") or ""
        source = s.get("source") or ""
        install_id = (
            f"{source}/{skill_id_val}" if skill_id_val and source else source or skill_id_val
        )
        out.append(
            {
                "name": s.get("name") or skill_id_val or "unknown",
                "install_id": install_id,
                "description": s.get("description") or "",
                "installs": int(s.get("installs") or 0),
            }
        )
    return out


def _split_install_id(install_id: str) -> tuple[str, str]:
    """``owner/repo/skill`` → (``owner/repo``, ``skill``); ``owner/repo`` → (id, "").
    Mirrors ``SkillSearchToolkit.install_skill``'s parsing so a registry id installs
    identically here."""
    parts = (install_id or "").split("/")
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}", "/".join(parts[2:])
    if len(parts) == 2:
        return install_id, ""
    raise SkillSourceError(
        f"Invalid skill id {install_id!r}. Expected 'owner/repo/skill-name' or 'owner/repo'."
    )


async def registry_install(runtime, install_id: str) -> dict:
    """Download a registry skill and install it into ``runtime``'s skills dir, replacing
    any same-named skill already there. Records provenance in the runtime's lock file
    (same as the agent's ``install_skill`` tool) and invalidates discovery so the next
    catalog build sees it. Returns ``{name, description}``.

    Raises ``SkillDownloadError`` / ``SkillInstallError`` on a bad id or fetch failure —
    ``download_skill`` extracts into a temp dir and only calls ``runtime.install`` on
    success, so a failure leaves nothing half-installed in the target."""
    source, sid = _split_install_id(install_id)
    client = SkillsClient()
    runtime.ensure_storage()
    meta, computed_hash = await client.download_skill(source, sid, runtime)
    SkillsLock(runtime.lock_dir / "skills-lock.json").record(meta.name, source, computed_hash)
    runtime.invalidate()
    return {"name": meta.name, "description": meta.description or ""}


# --- git / upload (discover-and-pick) -------------------------------------------


def _validate_skill_name(name: str) -> None:
    if not (1 <= len(name) <= 64) or not _SKILL_NAME_RE.fullmatch(name):
        raise SkillSourceError(
            f"invalid skill name {name!r}: expected lowercase alphanumeric words "
            "separated by single hyphens"
        )


def _scan_skills(root: Path) -> list[dict]:
    """Every skill discoverable **anywhere** under ``root`` (a repo/archive may nest
    skills at the top level OR under a ``skills/`` subdir — a monorepo), as
    ``{name, description, dir}`` rows. ``dir`` is the absolute skill directory to copy
    from on install.

    Recurses via ``rglob`` (the runtime's own loader only scans immediate children, so
    it would miss a monorepo layout) and reads each ``SKILL.md``'s frontmatter directly;
    a file with no ``name`` or unreadable frontmatter is skipped, not fatal — lenient
    discovery, the same posture the loader takes. First name wins on a duplicate."""
    root = root.resolve()
    rows: list[dict] = []
    seen: set[str] = set()
    for skill_md in sorted(root.rglob("SKILL.md")):
        if not skill_md.is_file():
            continue
        if any(part in _EXCLUDE_DIRS for part in skill_md.relative_to(root).parts):
            continue
        try:
            fm = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        name = str(fm.get("name") or "").strip()
        if not name:
            continue
        _validate_skill_name(name)
        if name in seen:
            continue
        seen.add(name)
        rows.append(
            {
                "name": name,
                "description": str(fm.get("description") or "").strip(),
                "dir": str(skill_md.parent),
            }
        )
    return rows


def _validate_git_url(git_url: str) -> None:
    """Refuse a ``git_url`` that would run a command or reach an unintended transport.
    git's ``ext::`` / ``fd::`` transport helpers run a shell command at clone time on
    the HOST — before any skill sandbox — so ``ext::sh -c id`` is RCE; ``file://`` and
    exotic schemes are SSRF/local-read variants. Allow a plain https/ssh/git URL, an
    scp-style ``user@host:path``, or a bare local filesystem path (the tests clone a
    local repo); reject transport-helper (``name::…``) forms and any scheme outside the
    allowlist."""
    lowered = git_url.lower()
    if re.match(r"^[a-z][a-z0-9+.-]*::", lowered):  # ext::, fd::, … — the RCE vector
        raise SkillSourceError("that git URL uses an unsupported transport")
    m = re.match(r"^([a-z][a-z0-9+.-]*)://", lowered)  # explicit scheme → must allowlist
    if m and m.group(1) not in _ALLOWED_GIT_SCHEMES:
        raise SkillSourceError(f"unsupported git URL scheme: {m.group(1)}")


def _fetch_git(git_url: str, dest: Path) -> None:
    """Shallow-clone ``git_url`` into ``dest`` (isolated temp). A local path works too
    (tests point at a local repo). Raises :class:`SkillSourceError` on any failure."""
    git_url = (git_url or "").strip()
    if not git_url:
        raise SkillSourceError("a git URL is required")
    _validate_git_url(git_url)
    try:
        proc = subprocess.run(
            # ``-c protocol.ext.allow=never`` is belt-and-suspenders behind
            # _validate_git_url: even a URL that slips the check can't invoke the ext
            # transport helper. ``--`` ends option parsing but does NOT restrict
            # transports, so it is not enough on its own.
            [
                "git",
                "-c",
                "protocol.ext.allow=never",
                "clone",
                "--depth",
                "1",
                "--",
                git_url,
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:  # git not installed
        raise SkillSourceError("git is not available on the server") from exc
    except subprocess.TimeoutExpired as exc:
        raise SkillSourceError(f"cloning {git_url} timed out") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = detail[-1] if detail else "clone failed"
        raise SkillSourceError(f"could not clone {git_url}: {msg}")


def _unpack_upload(upload_path: Path, filename: str, dest: Path) -> None:
    """Materialise an uploaded source under ``dest``: a ``.zip`` is extracted (with
    path-traversal guarded), a bare ``SKILL.md`` becomes a single-skill dir, anything
    else is rejected."""
    name = (filename or "").lower()
    if name.endswith(".zip") or zipfile.is_zipfile(upload_path):
        try:
            with zipfile.ZipFile(upload_path) as zf:
                total = 0
                for member in zf.infolist():
                    target = (dest / member.filename).resolve()
                    if not target.is_relative_to(dest.resolve()):
                        raise SkillSourceError("archive contains an unsafe path")
                    # Guard the UNCOMPRESSED size before extracting a byte: a small
                    # crafted zip can expand to exhaust disk. ``file_size`` is the
                    # header's claim, but extractall trusts it, so gate on it.
                    if member.file_size > _MAX_MEMBER_BYTES:
                        raise SkillSourceError("archive contains a file that is too large")
                    total += member.file_size
                    if total > _MAX_TOTAL_BYTES:
                        raise SkillSourceError("archive is too large to unpack")
                zf.extractall(dest)
        except zipfile.BadZipFile as exc:
            raise SkillSourceError("upload is not a readable zip archive") from exc
        return
    if name.endswith("skill.md"):
        # A lone SKILL.md → wrap it in a directory; the loader reads the skill's real
        # name from the frontmatter, not from this placeholder dir name.
        (dest / "skill").mkdir(parents=True, exist_ok=True)
        shutil.copy(upload_path, dest / "skill" / "SKILL.md")
        return
    raise SkillSourceError("upload must be a SKILL.md or a zipped skill folder")


def _materialise_source(
    tmp: Path, *, git_url: str | None, upload_path: str | os.PathLike | None, filename: str
) -> Path:
    src = tmp / "src"
    src.mkdir(parents=True, exist_ok=True)
    if git_url:
        _fetch_git(git_url, src)
    elif upload_path is not None:
        _unpack_upload(Path(upload_path), filename, src)
    else:
        raise SkillSourceError("provide a git URL or an uploaded file")
    return src


def discover_source(
    *, git_url: str | None = None, upload_path: str | os.PathLike | None = None, filename: str = ""
) -> list[dict]:
    """Fetch/unpack a git or upload source into an **isolated temp area**, scan it for
    every ``SKILL.md``, and return the discovered skills (``name`` + ``description``) for
    the checklist. Installs nothing; the temp area is torn down before returning. Raises
    :class:`SkillSourceError` for an unreachable/invalid source or one with no skill."""
    with tempfile.TemporaryDirectory(prefix="skill-discover-") as td:
        src = _materialise_source(
            Path(td), git_url=git_url, upload_path=upload_path, filename=filename
        )
        rows = _scan_skills(src)
    if not rows:
        raise SkillSourceError("no SKILL.md found in the source")
    return [{"name": r["name"], "description": r["description"]} for r in rows]


def install_from_source(
    runtime,
    names: list[str],
    *,
    git_url: str | None = None,
    upload_path: str | os.PathLike | None = None,
    filename: str = "",
) -> list[dict]:
    """Re-fetch the source into isolation, copy each of the selected ``names`` into
    ``runtime``'s skills dir as a snapshot (replacing a same-named skill), and invalidate
    discovery. Returns the installed ``{name, description}`` rows.

    Nothing lands in the target until a matching skill is found and copied, so an
    invalid source or an unknown name fails before mutating the target (no half-install).
    Raises :class:`SkillSourceError`."""
    wanted = [n for n in (names or []) if n and n.strip()]
    if not wanted:
        raise SkillSourceError("select at least one skill to install")
    with tempfile.TemporaryDirectory(prefix="skill-install-") as td:
        src = _materialise_source(
            Path(td), git_url=git_url, upload_path=upload_path, filename=filename
        )
        scanned = _scan_skills(src)
        by_name = {r["name"]: r for r in scanned}
        missing = [n for n in wanted if n not in by_name]
        if missing:
            raise SkillSourceError(f"skill(s) not found in the source: {', '.join(missing)}")
        all_dirs = {Path(r["dir"]).resolve() for r in scanned}
        runtime.ensure_storage()
        installed: list[dict] = []
        for n in wanted:
            row = by_name[n]
            _install_skill_dir(runtime, Path(row["dir"]), n, all_dirs)
            installed.append({"name": n, "description": row["description"]})
    runtime.invalidate()
    return installed


def _install_skill_dir(runtime, skill_dir: Path, name: str, all_dirs: set[Path]) -> None:
    """Install one discovered skill dir into ``runtime`` as ``name``, pruning any OTHER
    discovered skill dirs nested inside it. A source with a **root** ``SKILL.md`` (skill
    A) plus ``sub/SKILL.md`` (skill B) would otherwise copytree A's whole folder —
    dragging B's files inside A. When A has no nested skills (the common case) this is a
    plain ``runtime.install``; only the nesting case stages a pruned copy first, so the
    single writer stays ``runtime.install``."""
    _validate_skill_name(name)
    install_root = Path(runtime.install_dir).resolve()
    if (install_root / name).resolve().parent != install_root:
        raise SkillSourceError(f"invalid skill destination for {name!r}")
    skill_dir = skill_dir.resolve()
    nested = {d for d in all_dirs if d != skill_dir and skill_dir in d.parents}
    if not nested:
        runtime.install(skill_dir, name)  # copytree; replaces an existing same-name
        return

    def _ignore(directory: str, names: list[str]) -> set[str]:
        # Drop the nested skill subtrees, plus the usual VCS/build noise a root-level
        # skill dir (== a clone root) would otherwise carry in.
        base = Path(directory).resolve()
        return {nm for nm in names if nm in _EXCLUDE_DIRS or (base / nm).resolve() in nested}

    with tempfile.TemporaryDirectory(prefix="skill-prune-") as pd:
        staged = Path(pd) / name
        shutil.copytree(skill_dir, staged, ignore=_ignore)
        runtime.install(staged, name)
