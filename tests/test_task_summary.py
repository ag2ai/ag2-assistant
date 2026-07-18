"""Run summary distillation (cheap model, structured output, best-effort)."""

from assistant.config import Config
from assistant.tasks.summary import summarize_run


class _FakeReply:
    def __init__(self, out):
        self._out = out

    async def content(self):
        return self._out


class _FakeAgent:
    def __init__(self, out=None, boom=False):
        self._out = out
        self._boom = boom

    async def ask(self, prompt, response_schema=None):
        if self._boom:
            raise RuntimeError("llm down")
        return _FakeReply(self._out)


class _Out:
    summary = "Sent digest:  5 stories,\nleading with X"


async def test_summarize_normalises_whitespace_and_caps():
    s = await summarize_run(Config(), "do", "long reply", agent_factory=lambda: _FakeAgent(_Out()))
    assert s == "Sent digest: 5 stories, leading with X"


async def test_summarize_swallows_failures():
    s = await summarize_run(Config(), "do", "r", agent_factory=lambda: _FakeAgent(boom=True))
    assert s == ""
