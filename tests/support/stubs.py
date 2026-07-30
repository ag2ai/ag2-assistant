"""Real artifacts on disk (or in bytes): executable stub scripts instead of patching
shutil.which, and real archives instead of a stubbed download."""

import io
import shlex
import tarfile
import time
from pathlib import Path


def write_stub(
    path: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> Path:
    """Write an executable POSIX script printing the given output and exit code."""
    lines = ["#!/bin/sh"]
    if stdout:
        lines.append(f"printf '%s\\n' {shlex.quote(stdout)}")
    if stderr:
        lines.append(f"printf '%s\\n' {shlex.quote(stderr)} >&2")
    lines.append(f"exit {exit_code}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return path


def skill_tarball(
    name: str,
    *,
    description: str,
    nested: bool = False,
    repo: str = "owner-repo-cafe123",
) -> bytes:
    """A real ``.tar.gz`` shaped like GitHub's tarball API response: one top-level
    ``<repo>-<sha>/`` directory holding the skill. ``nested`` puts it in a ``<name>/``
    subdirectory (a monorepo of skills); otherwise ``SKILL.md`` sits at the repo root
    (a standalone single-skill repo). Fed to a download route, the production extractor
    unpacks it for real."""
    body = f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n".encode()
    inner = f"{repo}/{name}/SKILL.md" if nested else f"{repo}/SKILL.md"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(inner)
        info.size = len(body)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()
