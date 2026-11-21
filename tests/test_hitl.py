"""Tests for the HITL core and desktop asker (real local server, no browser)."""

import asyncio
import json
import re
from urllib.parse import quote

import httpx
import pytest
from ag2.events import HumanInputRequest

from assistant.hitl import DesktopAsker, HitlServer, Question, build_hitl_hook
from assistant.hitl.desktop import _js_str, _render_body


async def test_build_hitl_hook_adapts_asker():
    class FakeAsker:
        async def ask(self, question, timeout=None):
            return f"answer-to:{question.text}"

    hook = build_hitl_hook(FakeAsker())

    msg = await hook(HumanInputRequest(content="What is your name?"))
    assert msg.content == "answer-to:What is your name?"


async def test_a_yes_no_prompt_is_asked_as_options():
    asked = []

    class FakeAsker:
        async def ask(self, question, timeout=None):
            asked.append(question)
            return "yes"

    hook = build_hitl_hook(FakeAsker())

    await hook(
        HumanInputRequest(content="Agent requests permission for: gh pr list\nAllow? (yes/no)")
    )
    await hook(HumanInputRequest(content="What is your name?"))

    assert [q.options for q in asked] == [["yes", "no"], None]


def test_render_body_options_and_freetext():

    opts = _render_body(
        Question("Allow access?", options=["Allow once", "Deny"], kind="permission")
    )
    assert "Allow access?" in opts
    assert "Allow once" in opts and "Deny" in opts
    assert "Permission" in opts

    free = _render_body(Question("What city?"))
    assert "freetext" in free  # free-text input present


def test_render_body_options_carry_no_inline_handlers():
    """Options ride in an escaped data-answer attribute; no inline JS for CSP to block."""

    html_out = _render_body(Question("Pick", options=['Say "hi" <b>'], kind="permission"))
    assert "onclick" not in html_out and "onkeydown" not in html_out
    assert 'data-answer="Say &quot;hi&quot; &lt;b&gt;"' in html_out
    assert "onkeydown" not in _render_body(Question("What city?"))


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


XSS_ID = 'x";fetch("/api/tasks",{method:"POST"});x="</script><script>alert(1)</script>'
PATH_XSS_ID = 'x";alert(1);x="'


@pytest.mark.asyncio
async def test_unknown_request_page_does_not_reflect_the_id():
    server = HitlServer(port=8793)
    await server.ensure_running()
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8793") as c:
            page = await c.get(f"/hitl/{quote(PATH_XSS_ID, safe='')}")
            assert "Already handled" in page.text
            assert 'const ID = "";' in page.text
            assert "alert(1)" not in page.text
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_page_csp_allows_only_its_own_nonce():
    server = HitlServer(port=8794)
    await server.ensure_running()
    try:
        req_id, _ = server.register(Question("Q", options=["A"]))
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8794") as c:
            for path in (f"/hitl/{req_id}", "/hitl/unknown"):
                page = await c.get(path)
                csp = page.headers["content-security-policy"]
                nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
                assert "unsafe-inline" not in csp and "default-src 'none'" in csp
                assert f'<script nonce="{nonce}">' in page.text
                assert page.text.count("<script") == 1
            again = (await c.get("/hitl/unknown")).headers["content-security-policy"]
            assert again != csp  # a fresh nonce per response
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_server_rejects_cross_origin_answer():
    server = HitlServer(port=8795)
    await server.ensure_running()
    try:
        req_id, fut = server.register(Question("Q", options=["Allow", "Deny"]))
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8795") as c:
            url = f"/hitl/{req_id}/answer"
            evil = await c.post(
                url, json={"answer": "Allow"}, headers={"origin": "http://evil.example"}
            )
            assert evil.status_code == 403
            assert not fut.done()
            same = await c.post(
                url, json={"answer": "Deny"}, headers={"origin": "http://127.0.0.1:8795"}
            )
            assert same.json()["ok"] is True
        assert await asyncio.wait_for(fut, timeout=2) == "Deny"
    finally:
        await server.stop()


def test_js_str_cannot_close_the_script_element():
    lit = _js_str(XSS_ID)
    assert "</script>" not in lit and "<" not in lit
    assert json.loads(lit) == XSS_ID


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
