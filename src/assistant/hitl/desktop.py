"""Desktop Asker — serves styled HITL question pages in the browser.

A small local web server holds a registry of pending questions, each at its own
`/hitl/{id}` URL, so many can be open and answered concurrently. Answering one
resolves only that request. Styled to match ag2.ai (Playfair Display headings,
Open Sauce body, signature coral accent).
"""

import asyncio
import html
import uuid
import webbrowser

from pydantic import BaseModel

from assistant.hitl.base import Question

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AG2 Assistant — {title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600&display=swap" rel="stylesheet">
<link href="https://fonts.cdnfonts.com/css/open-sauce-sans" rel="stylesheet">
<style>
  :root {{
    --ink: #171717; --muted: #737373; --line: #e6e6e6;
    --accent: #f95339; --bg: #ffffff;
    --serif: "Playfair Display", Georgia, serif;
    --sans: "Open Sauce Sans", "Open Sauce One", system-ui, -apple-system, sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans);
    min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px;
  }}
  .card {{
    width: 100%; max-width: 540px; border: 1px solid var(--line); border-radius: 16px;
    padding: 40px; box-shadow: 0 12px 40px rgba(0,0,0,.06);
  }}
  .brand {{ display: flex; align-items: center; gap: 8px; margin-bottom: 28px; }}
  .brand .mark {{
    font-family: ui-monospace, "SF Mono", Menlo, monospace; font-weight: 700; letter-spacing: 1px;
    background: var(--ink); color: #fff; padding: 3px 8px; border-radius: 6px; font-size: 13px;
  }}
  .brand .kind {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 1.5px; }}
  h1 {{ font-family: var(--serif); font-weight: 600; font-size: 30px; line-height: 1.2; margin: 0 0 12px; }}
  .detail {{ color: var(--muted); font-size: 15px; line-height: 1.55; margin: 0 0 28px; }}
  .options {{ display: flex; flex-direction: column; gap: 10px; }}
  button, .opt {{
    font-family: var(--sans); font-size: 15px; font-weight: 600; cursor: pointer;
    padding: 14px 18px; border-radius: 10px; border: 1px solid var(--ink);
    background: var(--ink); color: #fff; transition: transform .04s ease, opacity .15s ease; text-align: left;
  }}
  button:hover {{ opacity: .9; }}
  button:active {{ transform: translateY(1px); }}
  button.primary {{ background: var(--accent); border-color: var(--accent); }}
  button.secondary {{ background: #fff; color: var(--ink); }}
  input[type=text] {{
    width: 100%; padding: 14px 16px; font-family: var(--sans); font-size: 15px;
    border: 1px solid var(--line); border-radius: 10px; margin-bottom: 12px;
  }}
  input[type=text]:focus {{ outline: 2px solid var(--accent); border-color: var(--accent); }}
  .done {{ text-align: center; }}
  .done .tick {{ font-size: 40px; color: var(--accent); }}
  .foot {{ margin-top: 26px; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <div class="card" id="card">{body}</div>
<script>
  const ID = "{req_id}";
  async function answer(value) {{
    await fetch(`/hitl/${{ID}}/answer`, {{
      method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{answer: value}})
    }});
    document.getElementById("card").innerHTML =
      `<div class="done"><div class="tick">&#10003;</div>
       <h1>Thanks</h1><p class="detail">Your response was sent to AG2 Assistant. You can close this tab.</p></div>`;
  }}
  function submitText() {{
    const v = document.getElementById("freetext").value.trim();
    if (v) answer(v);
  }}
</script>
</body>
</html>"""


def _render_body(question: Question) -> str:
    kind = "Permission" if question.kind == "permission" else "Question"
    parts = [
        '<div class="brand"><span class="mark">AG2 Assistant</span>'
        f'<span class="kind">{html.escape(kind)}</span></div>',
        f"<h1>{html.escape(question.text)}</h1>",
    ]
    if question.detail:
        parts.append(f'<p class="detail">{html.escape(question.detail)}</p>')

    if question.options:
        parts.append('<div class="options">')
        deny_words = {"deny", "no", "cancel", "decline", "reject"}
        for i, opt in enumerate(question.options):
            lower = opt.strip().lower()
            cls = "secondary" if lower in deny_words else ("primary" if i == 0 else "")
            esc = html.escape(opt)
            # The JS call goes inside a double-quoted onclick="…" attribute, so the
            # whole call (incl. its JSON double-quotes) must be HTML-escaped or the
            # quotes collide and break the page's JavaScript.
            onclick = html.escape(f"answer({_js_str(opt)})", quote=True)
            parts.append(f'<button class="{cls}" onclick="{onclick}">{esc}</button>')
        parts.append("</div>")
    else:
        parts.append(
            '<input type="text" id="freetext" placeholder="Type your answer…" '
            "autofocus onkeydown=\"if(event.key==='Enter')submitText()\">"
            '<div class="options"><button class="primary" onclick="submitText()">Send</button></div>'
        )

    parts.append('<div class="foot">Requested by your AG2 Assistant assistant</div>')
    return "".join(parts)


def _js_str(s: str) -> str:
    """Safely embed a Python string as a JS string literal."""
    import json

    return json.dumps(s)


class _Answer(BaseModel):
    answer: str


def _already_handled_page(req_id: str) -> str:
    return _PAGE.format(
        title="Done",
        req_id=req_id,
        body='<div class="done"><div class="tick">&#10003;</div>'
        '<h1>Already handled</h1><p class="detail">This request has '
        "been answered or has expired. You can close this tab.</p></div>",
    )


def add_hitl_routes(app, registry: "HitlServer") -> None:
    """Mount the styled HITL page + answer routes on any FastAPI app.

    Used both by the standalone `HitlServer` (desktop popup) and by the gateway,
    so a running gateway serves the same `/hitl/{id}` pages a UI client can drive.
    `registry` only needs `question_for(id)` and `answer(id, text)`.
    """
    from fastapi.responses import HTMLResponse, JSONResponse

    @app.get("/hitl/{req_id}", response_class=HTMLResponse)
    async def hitl_page(req_id: str):
        question = registry.question_for(req_id)
        if question is None:
            return HTMLResponse(_already_handled_page(req_id))
        return HTMLResponse(
            _PAGE.format(
                title=question.kind.title(),
                req_id=req_id,
                body=_render_body(question),
            )
        )

    @app.post("/hitl/{req_id}/answer")
    async def hitl_answer(req_id: str, payload: _Answer):
        if registry.answer(req_id, payload.answer):
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "reason": "unknown"}, status_code=404)


class HitlServer:
    """Local web server hosting concurrent HITL question pages."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        # port=0 → the OS assigns a free ephemeral port, so a lingering server
        # from a cancelled run can never block a new one ("address already in use").
        self.host = host
        self.port = port
        self._actual_port = port
        self._pending: dict[str, tuple[Question, asyncio.Future]] = {}
        self._server = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._server is not None and getattr(self._server, "started", False)

    def _build_app(self):
        from fastapi import FastAPI

        app = FastAPI()
        add_hitl_routes(app, self)
        return app

    # --- registry surface (shared by the desktop server and the gateway) ---

    def question_for(self, req_id: str) -> Question | None:
        entry = self._pending.get(req_id)
        return entry[0] if entry is not None else None

    def answer(self, req_id: str, answer: str) -> bool:
        """Resolve a pending question by id. False if no such open question."""
        entry = self._pending.get(req_id)
        if entry is None:
            return False
        _, fut = entry
        if not fut.done():
            fut.set_result(answer)
        return True

    def pending_list(self) -> list[dict]:
        """Open questions, for a UI client to render (relative `path` per item)."""
        return [
            {
                "id": req_id,
                "text": q.text,
                "detail": q.detail,
                "options": q.options,
                "kind": q.kind,
                "path": f"/hitl/{req_id}",
            }
            for req_id, (q, _) in self._pending.items()
        ]

    def path_for(self, req_id: str) -> str:
        return f"/hitl/{req_id}"

    async def ensure_running(self) -> None:
        async with self._lock:
            if self.started:
                return
            import uvicorn

            config = uvicorn.Config(
                self._build_app(), host=self.host, port=self.port, log_level="warning"
            )
            self._server = uvicorn.Server(config)
            self._task = asyncio.create_task(self._server.serve())
            while not self._server.started:
                await asyncio.sleep(0.05)
            # When port=0, read the port the OS actually assigned.
            try:
                self._actual_port = self._server.servers[0].sockets[0].getsockname()[1]
            except Exception:
                self._actual_port = self.port

    def register(self, question: Question) -> tuple[str, asyncio.Future]:
        req_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = (question, fut)
        return req_id, fut

    def discard(self, req_id: str) -> None:
        self._pending.pop(req_id, None)

    def url_for(self, req_id: str) -> str:
        return f"http://{self.host}:{self._actual_port}/hitl/{req_id}"

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await self._task
            except Exception:
                pass
        self._server = None
        self._task = None


class DesktopAsker:
    """Asks the human via a styled local web page opened in the browser."""

    def __init__(
        self,
        server: HitlServer | None = None,
        open_browser: bool = True,
    ) -> None:
        self._server = server or HitlServer()
        self._open_browser = open_browser

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        await self._server.ensure_running()
        req_id, fut = self._server.register(question)
        url = self._server.url_for(req_id)
        if self._open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        print(f"[AG2 Assistant] Awaiting your answer at {url}", flush=True)
        try:
            if timeout:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        finally:
            self._server.discard(req_id)

    async def aclose(self) -> None:
        await self._server.stop()
