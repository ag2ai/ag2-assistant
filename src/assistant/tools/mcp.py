"""Namespaced MCP tool adapters with persistent sessions.

AG2's native ``MCPToolkit`` intentionally exposes MCP server tools as ordinary
function tools with their raw names. That is ideal for a single isolated server,
but a personal assistant combines native tools and multiple MCP servers, where
generic MCP names like ``read_file`` or ``search`` can collide. This module keeps
AG2's MCP content handling while presenting stable namespaced tool names to the
model.

It also diverges from AG2's per-call session model: the stock toolkit spawns a
fresh server process around every tool call, which breaks stateful servers
(Playwright MCP's browser closes with the process after each call). Each toolkit
here holds one idle-expiring persistent session shared by all its tools — see
``_PersistentSession``.
"""

import asyncio
import re
import time
from collections.abc import Iterable
from contextlib import AsyncExitStack, ExitStack

from ag2.annotations import Context
from ag2.events import ToolCallEvent, ToolErrorEvent, ToolResultEvent
from ag2.middleware import BaseMiddleware, ToolExecution, ToolMiddleware
from ag2.tools import MCPStdioServerConfig
from ag2.tools.final.function_tool import FunctionDefinition, FunctionToolSchema
from ag2.tools.final.toolkit import Toolkit
from ag2.tools.tool import Tool

from assistant.observability import log_suppressed
from assistant.tools._mcp_compat import (
    AnyMCPConfig,
    MCPTool,
    extract_content,
    mcp_session,
    resolve_config,
    wrap_middleware,
)

_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")

# How long a server may sit unused before its process is closed. Long enough to
# span the tool calls of one agent turn (and a think between turns), short enough
# that orphaned toolkits after an agent reload self-clean without a dispose hook.
_IDLE_CLOSE_S = 300.0

# How long an unreachable server is left alone before discovery retries, so a
# broken one doesn't pay its startup cost on every turn.
_RETRY_AFTER_S = 60.0


class _PersistentSession:
    """One long-lived MCP client session per server, shared across tool calls.

    AG2's stock MCP tools open a fresh session — i.e. spawn a fresh server
    process — around every call. Stateless servers don't care, but stateful ones
    break: Playwright MCP opens a browser that dies with the process the moment
    a call returns, so navigate → click can never work. This keeps ONE session
    open, closing it after _IDLE_CLOSE_S of inactivity.

    The session context manager is entered AND exited inside a dedicated runner
    task: anyio's stdio transport requires both to happen in the same task.
    Callers just await the shared session and invoke tools on it.
    """

    __slots__ = ("_config", "_lock", "_task", "_ready", "_close", "_last_used")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._close: asyncio.Event | None = None
        self._last_used = 0.0

    def _touch(self) -> None:
        self._last_used = time.monotonic()

    async def _run(self, resolved, ready: asyncio.Future) -> None:
        try:
            async with mcp_session(resolved) as session:
                ready.set_result(session)
                while True:  # idle-expire, or exit promptly when aclose() asks
                    remaining = _IDLE_CLOSE_S - (time.monotonic() - self._last_used)
                    if remaining <= 0:
                        return
                    try:
                        # Cap each wait so a changed idle window is re-read ≤1s later.
                        await asyncio.wait_for(self._close.wait(), min(remaining, 1.0))
                        return
                    except TimeoutError:
                        continue
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)

    async def _session(self, resolved):
        async with self._lock:
            if self._task is None or self._task.done():
                self._close = asyncio.Event()
                self._ready = asyncio.get_running_loop().create_future()
                self._touch()
                self._task = asyncio.create_task(self._run(resolved, self._ready))
            self._touch()
            ready = self._ready
        try:
            return await asyncio.shield(ready)
        except Exception:
            async with self._lock:
                if self._ready is ready:  # failed to open — let the next call retry
                    self._task = None
            raise

    async def call_tool(self, resolved, name: str, arguments):
        """Call a tool on the shared session, reopening once if it went stale
        (idle-closed a moment ago, or the server process died)."""
        for attempt in (1, 2):
            session = await self._session(resolved)
            try:
                result = await session.call_tool(name, arguments)
                self._touch()
                return result
            except Exception:
                await self.aclose()
                if attempt == 2:
                    raise

    async def list_tools(self, resolved):
        session = await self._session(resolved)
        result = await session.list_tools()
        self._touch()
        return result

    async def aclose(self) -> None:
        """Close the session (and its server process) now. Safe to call anytime."""
        async with self._lock:
            task, self._task = self._task, None
            if task is None or task.done():
                return
            self._close.set()
        try:
            await asyncio.wait_for(task, 10)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()


def describe_mcp_error(exc: BaseException) -> str:
    """Flatten a server failure to the message a human can act on."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


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

    __slots__ = ("config", "_discovered", "_discover_lock", "_psession", "_error", "_error_at")

    def __init__(
        self,
        config: AnyMCPConfig,
        *,
        middleware: Iterable[ToolMiddleware] = (),
    ) -> None:
        self.config = config
        self._discovered = False
        self._discover_lock = asyncio.Lock()
        self._psession = _PersistentSession()  # shared by discovery + all proxies
        self._error: Exception | None = None
        self._error_at = 0.0
        label = config.server_label if isinstance(config.server_label, str) else ""
        super().__init__(name=label or "mcp_toolkit", middleware=middleware)

    async def schemas(self, context: Context):
        await self._discover_tools(context)
        return await super().schemas(context)

    async def aclose(self) -> None:
        """Close the server session/process now (idle expiry handles it otherwise)."""
        await self._psession.aclose()

    @property
    def last_error(self) -> Exception | None:
        """Why the most recent discovery attempt failed, or ``None`` if it worked.

        Discovery never raises, so callers that genuinely need to know whether the
        server is reachable — the settings health check — read this rather than
        wrapping ``schemas()`` in a try/except that would never fire.
        """
        return self._error

    async def _discover_tools(self, context: Context) -> None:
        if self._discovered:
            return

        async with self._discover_lock:
            if self._discovered:
                return
            if self._error is not None and time.monotonic() - self._error_at < _RETRY_AFTER_S:
                return

            try:
                resolved = resolve_config(self.config, context)
                raw_tools = (await self._psession.list_tools(resolved)).tools
            except Exception as exc:
                # Every toolkit's schemas() is collected before the model is called,
                # so raising here would abort turns that never touch MCP.
                self._error = exc
                self._error_at = time.monotonic()
                label = getattr(self.config, "server_label", None) or "mcp"
                log_suppressed("MCP tool discovery", exc, server=label)
                return

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
                    psession=self._psession,
                    middleware=self._middleware,
                )
                self._tools[proxy.name] = proxy

            self._discovered = True
            self._error = None


class _NamespacedMCPProxyTool(Tool):
    __slots__ = ("name", "raw_name", "schema", "_config", "_middleware", "_psession")

    def __init__(
        self,
        config: AnyMCPConfig,
        server_name: str,
        raw_tool: MCPTool,
        psession: _PersistentSession,
        middleware: tuple[ToolMiddleware, ...] = (),
    ) -> None:
        self._config = config
        self._middleware = middleware
        self._psession = psession
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
            result = await self._psession.call_tool(
                resolved, self.raw_name, event.serialized_arguments
            )
        except Exception as exc:
            return ToolErrorEvent.from_call(event, error=exc)

        if result.isError:
            return ToolErrorEvent.from_call(event, error=RuntimeError(str(result)))

        return ToolResultEvent.from_call(event, result=extract_content(result))


__all__ = [
    "NamespacedMCPToolkit",
    "build_mcp_tools",
    "describe_mcp_error",
    "namespaced_tool_name",
    "tool_prefix",
]
