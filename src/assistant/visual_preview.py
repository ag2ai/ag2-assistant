"""Render passive A2UI snapshots with a configured Chromium browser."""

import asyncio
import base64
import json
import socket
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
import uvicorn
from ag2.events import ImageInput, TextInput, ToolResult
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from websockets.asyncio.client import connect

from assistant.card_instances import check_json


class PreviewOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    width: int = Field(default=960, ge=240, le=2560)
    height: int = Field(default=720, ge=240, le=2160)
    theme: str = Field(default="dark", pattern="^(dark|light)$")


class PreviewError(ValueError):
    """A preview that cannot be produced."""


class _DevTools:
    """Execute serial CDP commands while refusing external resource requests."""

    def __init__(self, websocket, origin: str):
        self.websocket = websocket
        self.origin = origin
        self.sequence = 0
        self.errors: list[str] = []
        self.blocked: set[str] = set()
        self.network_errors: list[str] = []

    async def send(self, method: str, params: dict | None = None) -> int:
        self.sequence += 1
        await self.websocket.send(
            json.dumps({"id": self.sequence, "method": method, "params": params or {}})
        )
        return self.sequence

    async def call(self, method: str, params: dict | None = None) -> dict:
        target = await self.send(method, params)
        while True:
            reply = json.loads(await self.websocket.recv())
            if reply.get("id") == target:
                if "error" in reply:
                    raise PreviewError(f"Browser command failed: {reply['error']['message']}")
                return reply.get("result", {})
            event = reply.get("method")
            values = reply.get("params", {})
            if event == "Fetch.requestPaused":
                url = values["request"]["url"]
                allowed = url.startswith(self.origin + "/") or url.startswith("data:")
                await self.send(
                    "Fetch.continueRequest" if allowed else "Fetch.failRequest",
                    {
                        "requestId": values["requestId"],
                        **({} if allowed else {"errorReason": "BlockedByClient"}),
                    },
                )
                if not allowed and len(self.blocked) < 32:
                    self.blocked.add(url.split("?")[0][:200])
            elif event == "Network.responseReceived" and len(self.network_errors) < 32:
                response = values["response"]
                if response["status"] >= 400:
                    self.network_errors.append(
                        f"HTTP {response['status']}: {response['url'].split('?')[0][:200]}"
                    )
            elif event == "Runtime.exceptionThrown" and len(self.errors) < 32:
                detail = values["exceptionDetails"]
                self.errors.append(
                    detail.get("exception", {}).get("description", detail["text"])[:500]
                )
            elif (
                event == "Runtime.consoleAPICalled"
                and values.get("type") == "error"
                and len(self.errors) < 32
            ):
                self.errors.append(
                    " ".join(
                        str(arg.get("value", arg.get("description", ""))) for arg in values["args"]
                    )[:500]
                )


class _PreviewServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        """Leave the application's signal handlers in place."""
        yield


class VisualPreview:
    """Produce an image ToolResult without saving or refreshing its source data."""

    def __init__(self, browser: str, *, assets: Path | None = None):
        self.browser = browser
        self.assets = assets or Path(__file__).parent / "gateway" / "static" / "app"

    async def render(self, component: dict, data: dict, **options: Any) -> ToolResult:
        settings = PreviewOptions.model_validate(options)
        check_json({"component": component, "data": data})
        if not self.browser:
            raise PreviewError(
                "Choose a Chromium executable in Settings → General → Visual preview."
            )
        executable = Path(self.browser)
        if not executable.is_absolute() or not executable.is_file():
            raise PreviewError("The configured Chromium executable does not exist.")
        if not (self.assets / "preview.html").is_file():
            raise PreviewError("Preview assets are missing; rebuild the web application.")
        payload = {"component": component, "data": data, **settings.model_dump()}
        async with asyncio.timeout(40):
            return await self._render(payload)

    async def _render(self, payload: dict) -> ToolResult:
        async def snapshot(request):
            return JSONResponse(payload)

        app = Starlette(
            routes=[
                Route("/snapshot", snapshot),
                Mount("/app", StaticFiles(directory=self.assets)),
            ]
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = _PreviewServer(
            uvicorn.Config(app, log_level="error", access_log=False, lifespan="off")
        )
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        process = None
        temporary = None
        try:
            while not server.started:
                if server_task.done():
                    await server_task
                    raise PreviewError("Preview server did not start.")
                await asyncio.sleep(0.01)
            temporary = TemporaryDirectory(prefix="ag2-visual-preview-")
            directory = temporary.name
            process = await asyncio.create_subprocess_exec(
                self.browser,
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-extensions",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={directory}",
                "about:blank",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env={},
            )
            port_file = Path(directory) / "DevToolsActivePort"
            for _ in range(200):
                if process.returncode is not None:
                    raise PreviewError(
                        "Chromium exited before rendering. Check the configured executable."
                    )
                if port_file.exists():
                    break
                await asyncio.sleep(0.05)
            else:
                raise PreviewError("Chromium did not expose its debugging connection.")
            port = int(port_file.read_text().splitlines()[0])
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/json/list")
                response.raise_for_status()
                page = next(item for item in response.json() if item["type"] == "page")
            async with connect(
                page["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024, proxy=None
            ) as websocket:
                cdp = _DevTools(websocket, origin)
                await cdp.call("Runtime.enable")
                await cdp.call("Page.enable")
                await cdp.call("Network.enable")
                await cdp.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
                await cdp.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": payload["width"],
                        "height": payload["height"],
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                await cdp.call("Page.navigate", {"url": origin + "/app/preview.html"})
                while True:
                    result = await cdp.call(
                        "Runtime.evaluate",
                        {
                            "expression": "window.__a2uiPreview || null",
                            "returnByValue": True,
                        },
                    )
                    diagnostics = result.get("result", {}).get("value")
                    if diagnostics is not None:
                        break
                    if cdp.errors or cdp.network_errors:
                        raise PreviewError(
                            "Preview failed: " + (cdp.errors + cdp.network_errors)[0]
                        )
                    await asyncio.sleep(0.05)
                height = min(4096, max(payload["height"], diagnostics["height"]))
                captured = await cdp.call(
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "captureBeyondViewport": True,
                        "clip": {
                            "x": 0,
                            "y": 0,
                            "width": payload["width"],
                            "height": height,
                            "scale": 1,
                        },
                    },
                )
                image = base64.b64decode(captured["data"], validate=True)
                if len(image) > 8 * 1024 * 1024:
                    raise PreviewError(
                        "Preview image is too large; reduce the viewport or content."
                    )
                diagnostics.update(
                    {
                        "errors": cdp.errors,
                        "blocked_resources": sorted(cdp.blocked),
                        "network_errors": cdp.network_errors,
                        "image_height": height,
                        "truncated": diagnostics["height"] > height,
                    }
                )
                return ToolResult(
                    ImageInput(data=image, media_type="image/png"),
                    TextInput(json.dumps(diagnostics, ensure_ascii=False)),
                )
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            server.should_exit = True
            try:
                await asyncio.wait_for(server_task, 5)
            except TimeoutError:
                server_task.cancel()
                await asyncio.gather(server_task, return_exceptions=True)
            sock.close()
            if temporary is not None:
                temporary.cleanup()
