"""Feedback learner: a 👍/👎 + reason is distilled into the right memory heading.

The LLM distillation itself needs a model; here we exercise the deterministic parts —
category routing by sentiment and the no-LLM fallback — by forcing the model path to
fail. The fallback is asymmetric on purpose: a 👎 complaint still has signal raw, but a
👍 with nothing to generalise ("Spot on!") is noise, so it is dropped rather than stored
verbatim. Memory is per-profile: each test points the config's data_dir at a tmp dir and
reads that profile's store.
"""

import pytest

from assistant import feedback, memory
from assistant.config import Config
from tests.support.fakes import FakeStructuredAgent


@pytest.fixture
def cfg(paths, tmp_path):
    return Config.for_paths(paths, data_dir=tmp_path)


def _store_path(cfg):
    return cfg.data_dir / "profile.db"


def _dead_learner(config):
    """A learner whose model is unreachable — exercises the no-LLM fallback path."""
    raise RuntimeError("no model in test")


async def test_feedback_down_falls_back_to_raw_complaint(cfg):
    # Force the LLM path to raise so we hit the fallback — no network/model needed.
    # A 👎 complaint keeps its signal even raw, so it lands under "What they dislike".
    await feedback.learn(
        cfg,
        sentiment="down",
        reason="too verbose and corporate",
        content="x",
        request="y",
        agent_factory=_dead_learner,
    )

    profile = await memory.read_profile(_store_path(cfg))
    dislikes = profile.split("## What they dislike", 1)[1].split("\n## ", 1)[0]
    assert "too verbose and corporate" in dislikes  # 👎 → What they dislike


async def test_feedback_up_drops_raw_praise_instead_of_storing_it(cfg):
    """The reported bug: a 👍 whose LLM leg fails must NOT dump the raw reason. A like
    with nothing to generalise ("Spot on!") is noise, so the fallback is skipped and the
    profile is left untouched — no verbatim "* Spot on!" bullet."""
    before = await memory.read_profile(_store_path(cfg))
    await feedback.learn(
        cfg,
        sentiment="up",
        reason="Spot on!",
        content="x",
        request="y",
        agent_factory=_dead_learner,
    )
    assert await memory.read_profile(_store_path(cfg)) == before  # 👍 praise → nothing stored


async def test_feedback_empty_reason_writes_nothing(cfg):
    before = await memory.read_profile(_store_path(cfg))
    await feedback.learn(
        cfg, sentiment="down", reason="   ", content="x", request="y", agent_factory=_dead_learner
    )
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


async def test_a_successful_learner_records_its_generalised_note(cfg):
    """The LLM path: the model's generalised note lands in the profile, not the raw reason."""
    out = feedback.FeedbackMemory(note="Prefers terse, concrete answers", remove=[])
    await feedback.learn(
        cfg,
        sentiment="down",
        reason="too verbose and corporate",
        content="x",
        request="y",
        agent_factory=lambda config: FakeStructuredAgent(out),
    )

    profile = await memory.read_profile(_store_path(cfg))
    dislikes = profile.split("## What they dislike", 1)[1].split("\n## ", 1)[0]
    assert "Prefers terse, concrete answers" in dislikes
    assert "too verbose and corporate" not in profile  # the raw reason is not stored
