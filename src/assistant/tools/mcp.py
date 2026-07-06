"""Namespaced MCP tool adapters.

AG2's native ``MCPToolkit`` intentionally exposes MCP server tools as ordinary
function tools with their raw names. That is ideal for a single isolated server,
but a personal assistant combines native tools and multiple MCP servers, where
generic MCP names like ``read_file`` or ``search`` can collide. This module keeps
AG2's MCP session/content handling while presenting stable namespaced tool names
to the model.
"""

import asyncio
import re
from collections.abc import Iterable
from contextlib import AsyncExitStack, ExitStack

from ag2.annotations import Context
from ag2.events import ToolCallEvent, ToolErrorEvent, ToolResultEvent
from ag2.middleware import BaseMiddleware, ToolExecution, ToolMiddleware
from ag2.tools import MCPStdioServerConfig
from ag2.tools.final.function_tool import FunctionDefinition, FunctionToolSchema
from ag2.tools.final.toolkit import Toolkit
from ag2.tools.tool import Tool

from assistant.tools._mcp_compat import (
    AnyMCPConfig,
    MCPTool,
    extract_content,
    mcp_session,
    resolve_config,
    wrap_middleware,
)

_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")


def tool_prefix(server_name: str) -> str:
    """Provider-safe function-name prefix for an MCP server label."""
    prefix = _NAME_RE.sub("_", server_name.strip().lower()).strip("_")
    if not prefix:
        prefix = "mcp"
    if prefix[0].isdigit():
        prefix = f"mcp_{prefix}"
    return prefix


def namespaced_tool_name(server_name: str, raw_name: str) -> str:
    """Stable public tool name for one MCP server tool."""
    raw = _NAME_RE.sub("_", raw_name.strip()).strip("_")
    return f"{tool_prefix(server_name)}_{raw}"


def build_mcp_tools(servers: Iterable[dict]) -> list[Tool]:
    tools: list[Tool] = []
    for server in servers:
        if not server.get("enabled", True):
            continue
        tools.append(
            NamespacedMCPToolkit(
                MCPStdioServerConfig(
                    command=server["command"],
                    args=server.get("args") or [],
                    env=server.get("env") or None,
                    cwd=server.get("cwd") or None,
                    server_label=server["name"],
                    allowed_tools=server.get("allowed_tools") or None,
                    blocked_tools=server.get("blocked_tools") or None,
                )
            )
        )
    return tools


class NamespacedMCPToolkit(Toolkit):
    """Expose one MCP server as namespaced AG2 function tools."""

    __slots__ = ("config", "_discovered", "_discover_lock")

    def __init__(
        self,
        config: AnyMCPConfig,
        *,
        middleware: Iterable[ToolMiddleware] = (),
    ) -> None:
        self.config = config
        self._discovered = False
        self._discover_lock = asyncio.Lock()
        label = config.server_label if isinstance(config.server_label, str) else ""
        super().__init__(name=label or "mcp_toolkit", middleware=middleware)

    async def schemas(self, context: Context):
        await self._discover_tools(context)
        return await super().schemas(context)

    async def _discover_tools(self, context: Context) -> None:
        if self._discovered:
            return

        async with self._discover_lock:
            if self._discovered:
                return

            resolved = resolve_config(self.config, context)
            async with mcp_session(resolved) as session:
                raw_tools = (await session.list_tools()).tools

            allowed = resolved.allowed_tools
            blocked = set(resolved.blocked_tools or [])
            server_name = str(resolved.server_label or "mcp")
            for raw in raw_tools:
                if allowed is not None and raw.name not in allowed:
                    continue
                if raw.name in blocked:
                    continue
                proxy = _NamespacedMCPProxyTool(
                    config=self.config,
                    server_name=server_name,
                    raw_tool=raw,
                    middleware=self._middleware,
                )
                self._tools[proxy.name] = proxy

            self._discovered = True


class _NamespacedMCPProxyTool(Tool):
    __slots__ = ("name", "raw_name", "schema", "_config", "_middleware")

    def __init__(
        self,
        config: AnyMCPConfig,
        server_name: str,
        raw_tool: MCPTool,
        middleware: tuple[ToolMiddleware, ...] = (),
    ) -> None:
        self._config = config
        self._middleware = middleware
        self.raw_name = raw_tool.name
        self.name = namespaced_tool_name(server_name, raw_tool.name)
        self.schema = FunctionToolSchema(
            function=FunctionDefinition(
                name=self.name,
                description=(
                    f"MCP server '{server_name}' tool '{raw_tool.name}'. "
                    f"{raw_tool.description or ''}"
                ).strip(),
                parameters=dict(raw_tool.inputSchema or {}),
            )
        )

    async def schemas(self, context: Context) -> list[FunctionToolSchema]:
        return [self.schema]

    def register(
        self,
        stack: ExitStack | AsyncExitStack,
        context: Context,
        *,
        middleware: Iterable[BaseMiddleware] = (),
    ) -> None:
        execution: ToolExecution = self
        for hook in reversed(self._middleware):
            execution = wrap_middleware(hook, execution)
        for mw in middleware:
            execution = wrap_middleware(mw.on_tool_execution, execution)

        async def execute(event: ToolCallEvent, context: Context) -> None:
            result = await execution(event, context)
            await context.send(result)

        stack.enter_context(
            context.stream.where(ToolCallEvent.name == self.name).sub_scope(execute)
        )

    async def __call__(
        self, event: ToolCallEvent, context: Context
    ) -> ToolResultEvent | ToolErrorEvent:
        try:
            resolved = resolve_config(self._config, context)
            async with mcp_session(resolved) as session:
                result = await session.call_tool(self.raw_name, event.serialized_arguments)
        except Exception as exc:
            return ToolErrorEvent.from_call(event, error=exc)

        if result.isError:
            return ToolErrorEvent.from_call(event, error=RuntimeError(str(result)))

        return ToolResultEvent.from_call(event, result=extract_content(result))


__all__ = [
    "NamespacedMCPToolkit",
    "build_mcp_tools",
    "namespaced_tool_name",
    "tool_prefix",
]
