"""Feedback learner: a 👍/👎 + reason is distilled into the right memory heading.

The LLM distillation itself needs a model; here we exercise the deterministic parts —
category routing by sentiment and the no-LLM fallback — by forcing the model path to
fail. The fallback is asymmetric on purpose: a 👎 complaint still has signal raw, but a
👍 with nothing to generalise ("Spot on!") is noise, so it is dropped rather than stored
verbatim. Memory is per-profile: each test points the config's data_dir at a tmp dir and
reads that profile's store.
"""

import pytest

import assistant.agent as agent_mod
from assistant import feedback, memory
from assistant.config import load_config


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c.data_dir = tmp_path
    return c


def _store_path(cfg):
    return cfg.data_dir / "profile.db"


async def test_feedback_down_falls_back_to_raw_complaint(cfg, monkeypatch):
    # Force the LLM path to raise so we hit the fallback — no network/model needed.
    # A 👎 complaint keeps its signal even raw, so it lands under "What they dislike".
    def _boom(*a, **k):
        raise RuntimeError("no model in test")

    monkeypatch.setattr(agent_mod, "model_config", _boom)

    await feedback.learn(
        cfg, sentiment="down", reason="too verbose and corporate", content="x", request="y"
    )

    profile = await memory.read_profile(_store_path(cfg))
    dislikes = profile.split("## What they dislike", 1)[1].split("\n## ", 1)[0]
    assert "too verbose and corporate" in dislikes  # 👎 → What they dislike


async def test_feedback_up_drops_raw_praise_instead_of_storing_it(cfg, monkeypatch):
    """The reported bug: a 👍 whose LLM leg fails must NOT dump the raw reason. A like
    with nothing to generalise ("Spot on!") is noise, so the fallback is skipped and the
    profile is left untouched — no verbatim "* Spot on!" bullet."""
    monkeypatch.setattr(
        agent_mod, "model_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    before = await memory.read_profile(_store_path(cfg))
    await feedback.learn(cfg, sentiment="up", reason="Spot on!", content="x", request="y")
    assert await memory.read_profile(_store_path(cfg)) == before  # 👍 praise → nothing stored


async def test_feedback_empty_reason_writes_nothing(cfg, monkeypatch):
    monkeypatch.setattr(
        agent_mod, "model_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    before = await memory.read_profile(_store_path(cfg))
    await feedback.learn(cfg, sentiment="down", reason="   ", content="x", request="y")
    assert await memory.read_profile(_store_path(cfg)) == before  # no reason → no write


async def test_record_preference_revises_conflicting_bullet(tmp_path):
    store_path = tmp_path / "profile.db"
    # An earlier "like" that a later dislike contradicts.
    await memory.record_preference(store_path, "Likes long, detailed reports", category="how")
    assert "Likes long, detailed reports" in await memory.read_profile(store_path)

    # Memory-aware revise: drop the conflicting bullet (marker-insensitive), add the fix.
    await memory.record_preference(
        store_path,
        "Dislikes long reports; prefers brief ones",
        category="dislikes",
        remove=["Likes long, detailed reports"],
    )
    p = await memory.read_profile(store_path)
    assert "Likes long, detailed reports" not in p  # conflict removed
    assert "Dislikes long reports; prefers brief ones" in p  # correction added


async def test_record_preference_noop_when_empty(tmp_path):
    store_path = tmp_path / "profile.db"
    before = await memory.read_profile(store_path)
    await memory.record_preference(store_path, "", category="how", remove=[])  # pure skip
    assert await memory.read_profile(store_path) == before
