"""Run summary distillation (cheap model, structured output, best-effort)."""

from assistant.config import Config
from assistant.tasks.summary import suggest_task_meta, summarize_run


class _FakeReply:
    def __init__(self, out):
        self._out = out

    async def content(self):
        return self._out


class _FakeAgent:
    config = object()  # a real ag2.Agent always carries one; picks the native path

    def __init__(self, out=None, boom=False):
        self._out = out
        self._boom = boom

    async def ask(self, prompt, response_schema=None):
        if self._boom:
            raise RuntimeError("llm down")
        return _FakeReply(self._out)


class _Out:
    summary = "Sent digest:  5 stories,\nleading with X"


async def test_summarize_normalises_whitespace_and_caps(paths):
    s = await summarize_run(
        Config.for_paths(paths), "do", "long reply", agent_factory=lambda: _FakeAgent(_Out())
    )
    assert s == "Sent digest: 5 stories, leading with X"


async def test_summarize_swallows_failures(paths):
    s = await summarize_run(
        Config.for_paths(paths), "do", "r", agent_factory=lambda: _FakeAgent(boom=True)
    )
    assert s == ""


class _MetaOut:
    name = "Daily news digest"
    description = "Collects headlines every morning."


async def test_suggest_task_meta_returns_name_and_description(paths):
    name, desc = await suggest_task_meta(
        Config.for_paths(paths),
        "collect news each morning",
        agent_factory=lambda: _FakeAgent(_MetaOut()),
    )
    assert name == "Daily news digest"
    assert desc == "Collects headlines every morning."


async def test_suggest_task_meta_falls_back_on_llm_failure(paths):
    prompt = "x" * 100
    name, desc = await suggest_task_meta(
        Config.for_paths(paths), prompt, agent_factory=lambda: _FakeAgent(boom=True)
    )
    assert name == "x" * 40
    assert desc == ""


class _ClosingConfig:
    def __init__(self):
        self.closed = 0

    async def aclose(self):
        self.closed += 1


async def test_one_shot_agents_close_their_model_config(paths):
    # A claude_code-style config spawns an adapter subprocess per pass; the
    # one-shot summarizer must tear its own config down (the leak fix).
    cfg = _ClosingConfig()
    agent = _FakeAgent(_Out())
    agent.config = cfg
    await summarize_run(Config.for_paths(paths), "do", "r", agent_factory=lambda: agent)
    assert cfg.closed == 1
    cfg2 = _ClosingConfig()
    agent2 = _FakeAgent(_MetaOut())
    agent2.config = cfg2
    await suggest_task_meta(Config.for_paths(paths), "p", agent_factory=lambda: agent2)
    assert cfg2.closed == 1
