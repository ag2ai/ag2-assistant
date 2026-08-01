"""Tolerant YAML mapping read/write.

Its own module so ``paths`` and ``config`` can both use it without importing each
other (``Config`` carries a ``Paths``, and ``Paths`` reads a config.yaml).
"""

import os
from pathlib import Path

import yaml


def read_yaml(path: Path) -> dict:
    """Parse a YAML mapping file. A missing, malformed, or non-mapping file reads
    as an empty dict — the same tolerance a malformed config.json had."""
    try:
        data = yaml.safe_load(Path(path).read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_yaml(path: Path, data: dict) -> None:
    """Atomically write a YAML mapping (tmp file + os.replace, so a crashed write
    never leaves a truncated config behind)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    os.replace(tmp, path)
