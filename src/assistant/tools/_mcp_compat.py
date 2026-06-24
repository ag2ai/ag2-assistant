"""Compatibility wrapper around AG2 beta's MCP toolkit internals.

AG2 beta does not currently expose a public hook for renaming MCP proxy tools
while reusing its session/content/middleware handling. Keep that private coupling
in this one module so version drift fails with a clear diagnostic.
"""

from __future__ import annotations

from typing import Any

_REQUIRED = (
    "AnyMCPConfig",
    "MCPTool",
    "_extract_content",
    "_mcp_session",
    "_resolve_config",
    "_wrap_middleware",
)


class MCPCompatibilityError(RuntimeError):
    """Raised when the installed AG2 MCP internals no longer match this adapter."""


def _load() -> Any:
    try:
        from autogen.beta.tools.toolkits.mcp_server import toolkit
    except Exception as exc:
        raise MCPCompatibilityError(f"AG2 MCP toolkit is unavailable: {exc}") from exc

    missing = [name for name in _REQUIRED if not hasattr(toolkit, name)]
    if missing:
        raise MCPCompatibilityError(
            "AG2 MCP toolkit internals changed; missing: " + ", ".join(missing)
        )
    return toolkit


_toolkit = _load()

AnyMCPConfig = _toolkit.AnyMCPConfig
MCPTool = _toolkit.MCPTool


def extract_content(result):
    return _toolkit._extract_content(result)


def resolve_config(config, context):
    return _toolkit._resolve_config(config, context)


def mcp_session(config):
    return _toolkit._mcp_session(config)


def wrap_middleware(hook, inner):
    return _toolkit._wrap_middleware(hook, inner)


__all__ = [
    "AnyMCPConfig",
    "MCPCompatibilityError",
    "MCPTool",
    "extract_content",
    "mcp_session",
    "resolve_config",
    "wrap_middleware",
]
