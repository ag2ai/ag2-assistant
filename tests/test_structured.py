"""Tests for ask_structured — response_schema asks that also work on ACP models."""

import pytest
from ag2.acp import ClaudeCodeConfig
from pydantic import BaseModel

from assistant.structured import ask_structured


class _Title(BaseModel):
    title: str


class _FakeReply:
    def __init__(self, body="", content=None):
        self.body = body
        self._content = content

    async def content(self):
        return self._content


class _FakeAgent:
    """Records ask() kwargs; config decides which path ask_structured takes."""

    def __init__(self, config, reply):
        self.config = config
        self.reply = reply
        self.asked = None

    async def ask(self, prompt, **kwargs):
        self.asked = (prompt, kwargs)
        return self.reply


async def test_native_path_uses_response_schema():
    out = _Title(title="Native")
    agent = _FakeAgent(config=object(), reply=_FakeReply(content=out))
    result = await ask_structured(agent, "prompt", _Title)
    assert result is out
    assert agent.asked[1] == {"response_schema": _Title}


async def test_acp_path_embeds_schema_and_parses_fenced_json():
    reply = _FakeReply(body='```json\n{"title": "Russian Greeting"}\n```')
    agent = _FakeAgent(config=ClaudeCodeConfig(), reply=reply)
    result = await ask_structured(agent, "prompt", _Title)
    assert result.title == "Russian Greeting"
    prompt, kwargs = agent.asked
    # The ACP client ignores response_schema, so it must not be sent; the JSON
    # schema rides inside the prompt instead.
    assert kwargs == {}
    assert '"title"' in prompt and "JSON" in prompt


async def test_acp_path_parses_json_wrapped_in_prose():
    reply = _FakeReply(body='Here it is: {"title": "X"} — hope that helps!')
    agent = _FakeAgent(config=ClaudeCodeConfig(), reply=reply)
    result = await ask_structured(agent, "prompt", _Title)
    assert result.title == "X"


async def test_acp_path_plain_text_raises():
    # No JSON at all → raise; every caller already degrades gracefully.
    agent = _FakeAgent(config=ClaudeCodeConfig(), reply=_FakeReply(body="Russian Greeting"))
    with pytest.raises(ValueError):
        await ask_structured(agent, "prompt", _Title)


class _ClosingConfig:
    def __init__(self):
        self.closed = 0

    async def aclose(self):
        self.closed += 1


async def test_aclose_config_closes_acp_style_configs():
    from assistant.structured import aclose_config

    cfg = _ClosingConfig()
    await aclose_config(cfg)
    assert cfg.closed == 1


async def test_aclose_config_noop_without_aclose():
    from assistant.structured import aclose_config

    await aclose_config(object())  # ordinary provider configs — nothing to do
    await aclose_config(None)


async def test_aclose_config_suppresses_teardown_failures():
    from assistant.structured import aclose_config

    class _Bad:
        async def aclose(self):
            raise RuntimeError("boom")

    await aclose_config(_Bad())  # must not raise — teardown never masks results
