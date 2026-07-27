"""Runtime versions, read back from installed package metadata.

The single source of truth is ``[project].version`` in pyproject.toml (mirrors ag2's
own ``ag2/version.py``). This never hardcodes the number, so the two can't drift.
"""

from importlib.metadata import PackageNotFoundError, version

__all__ = ("__version__", "AG2_VERSION")

__version__ = version("ag2-assistant")

# The AG2 version underneath, shown in the "Powered by" dialog. Purely decorative, so
# it degrades to "" instead of taking the gateway down if the distribution can't be
# read — unlike __version__, which is load-bearing (the release workflow checks it).
try:
    AG2_VERSION = version("ag2")
except PackageNotFoundError:  # pragma: no cover — ag2 is a hard dependency
    AG2_VERSION = ""
