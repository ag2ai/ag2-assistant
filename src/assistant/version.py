"""Runtime version, read back from the installed package metadata.

The single source of truth is ``[project].version`` in pyproject.toml (mirrors ag2's
own ``ag2/version.py``). This never hardcodes the number, so the two can't drift.
"""

from importlib.metadata import version

__all__ = ("__version__",)

__version__ = version("ag2-assistant")
