"""Feedback learner: a 👍/👎 + reason is distilled into the right memory heading.

The LLM distillation itself needs a model; here we exercise the deterministic parts —
category routing by sentiment and the no-LLM fallback that guarantees the reason is
never lost — by forcing the model path to fail. Memory writes are isolated to a tmp
HOME by the autouse conftest fixture.
"""

from assistant import feedback, memory
from assistant.config import load_config


async def test_feedback_routes_by_sentiment_via_fallback(monkeypatch):
    # Force the LLM path to raise so we hit the fallback (which writes the raw reason
    # under the sentiment's heading) — no network/model needed.
    import assistant.agent as agent_mod

    def _boom(*a, **k):
        raise RuntimeError("no model in test")

    monkeypatch.setattr(agent_mod, "model_config", _boom)
    cfg = load_config()

    await feedback.learn(
        cfg, sentiment="down", reason="too verbose and corporate", content="x", request="y"
    )
    await feedback.learn(cfg, sentiment="up", reason="loved the brevity", content="x", request="y")

    profile = await memory.read_profile()
    dislikes = profile.split("## What they dislike", 1)[1].split("\n## ", 1)[0]
    how = profile.split("## How they like things done", 1)[1].split("\n## ", 1)[0]

    assert "too verbose and corporate" in dislikes  # 👎 → What they dislike
    assert "loved the brevity" in how  # 👍 → How they like things done


async def test_feedback_empty_reason_writes_nothing(monkeypatch):
    import assistant.agent as agent_mod

    monkeypatch.setattr(
        agent_mod, "model_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    cfg = load_config()
    before = await memory.read_profile()
    await feedback.learn(cfg, sentiment="down", reason="   ", content="x", request="y")
    assert await memory.read_profile() == before  # no reason → no memory write
