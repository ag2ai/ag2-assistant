"""Real executable stub scripts on disk, instead of patching shutil.which."""

import shlex
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
