"""Tests for the HITL core and desktop asker (real local server, no browser)."""

import asyncio

import httpx
import pytest

from assistant.hitl import DesktopAsker, HitlServer, Question, build_hitl_hook


async def test_build_hitl_hook_adapts_asker():
    class FakeAsker:
        async def ask(self, question, timeout=None):
            return f"answer-to:{question.text}"

    hook = build_hitl_hook(FakeAsker())

    from autogen.beta.events import HumanInputRequest

    msg = await hook(HumanInputRequest(content="What is your name?"))
    assert msg.content == "answer-to:What is your name?"


def test_render_body_options_and_freetext():
    from assistant.hitl.desktop import _render_body

    opts = _render_body(
        Question("Allow access?", options=["Allow once", "Deny"], kind="permission")
    )
    assert "Allow access?" in opts
    assert "Allow once" in opts and "Deny" in opts
    assert "Permission" in opts

    free = _render_body(Question("What city?"))
    assert "freetext" in free  # free-text input present


def test_render_body_onclick_is_escaped():
    """Regression: option buttons must not put raw double-quotes in onclick="…"
    (that collision broke the page JS so clicks never resolved)."""
    from assistant.hitl.desktop import _render_body

    html_out = _render_body(Question("Pick", options=["Allow once"], kind="permission"))
    assert 'onclick="answer("' not in html_out  # raw quote collision = broken
    assert "&quot;" in html_out  # escaped JS-string quotes


@pytest.mark.asyncio
async def test_server_concurrent_requests_resolve_independently():
    server = HitlServer(port=8791)
    await server.ensure_running()
    try:
        id1, fut1 = server.register(Question("Q1", options=["A", "B"]))
        id2, fut2 = server.register(Question("Q2", options=["X", "Y"]))
        assert id1 != id2

        async with httpx.AsyncClient(base_url="http://127.0.0.1:8791") as c:
            # both pages render with their own question text
            p1 = await c.get(f"/hitl/{id1}")
            p2 = await c.get(f"/hitl/{id2}")
            assert "Q1" in p1.text and "Q2" in p2.text

            # answering id1 resolves only fut1
            r = await c.post(f"/hitl/{id1}/answer", json={"answer": "A"})
            assert r.json()["ok"] is True

        await asyncio.wait_for(fut1, timeout=2)
        assert fut1.result() == "A"
        assert not fut2.done()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_unknown_request_returns_handled_page_and_404_on_answer():
    server = HitlServer(port=8792)
    await server.ensure_running()
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8792") as c:
            page = await c.get("/hitl/nonexistent")
            assert "Already handled" in page.text
            r = await c.post("/hitl/nonexistent/answer", json={"answer": "x"})
            assert r.status_code == 404
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_desktop_asker_end_to_end():
    asker = DesktopAsker(HitlServer(port=8793), open_browser=False)
    task = asyncio.create_task(asker.ask(Question("Pick", options=["A", "B"])))
    try:
        # wait for the server to come up and the request to register
        for _ in range(40):
            if asker._server.started and asker._server._pending:
                break
            await asyncio.sleep(0.05)
        req_id = next(iter(asker._server._pending))
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8793") as c:
            await c.post(f"/hitl/{req_id}/answer", json={"answer": "B"})
        result = await asyncio.wait_for(task, timeout=2)
        assert result == "B"
    finally:
        await asker.aclose()
