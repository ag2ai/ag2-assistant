"""Tests for the first-run onboarding interview."""

import pytest

from assistant import onboarding
from assistant.memory import PROFILE_PATH, build_profile_store


class ScriptedAsker:
    """Answers each question from a list, in order; raises if it runs dry."""

    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self.asked: list = []

    async def ask(self, question, timeout=None):
        self.asked.append(question)
        return self._answers.pop(0)


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    """Point the onboarding marker + profile store at a tmp dir."""
    marker = tmp_path / "onboarded"
    monkeypatch.setattr(onboarding, "marker_path", lambda: marker)
    store_path = tmp_path / "profile.db"
    env_path = tmp_path / ".env"
    return store_path, env_path, marker


def test_build_profile_renders_sections():
    md = onboarding.build_profile(
        {
            "name": "Mark",
            "location": "Sydney, Australia",
            "hours": "9am–6pm",
            "style": "Short & direct",
        }
    )
    assert "## About the user" in md
    assert "Name: Mark" in md
    assert "Location: Sydney, Australia" in md
    assert "## When they like things done" in md
    assert "9am–6pm" in md
    assert "Prefers short, direct answers." in md


def test_build_profile_empty_when_all_skipped():
    assert onboarding.build_profile({}) == ""
    assert onboarding.build_profile({"style": "No preference"}) == ""


async def test_needs_onboarding_true_when_no_profile(isolate):
    store_path, _, _ = isolate
    assert await onboarding.needs_onboarding(store_path) is True


async def test_needs_onboarding_false_after_marker(isolate):
    store_path, _, marker = isolate
    marker.write_text("done\n")
    assert await onboarding.needs_onboarding(store_path) is False


async def test_run_onboarding_seeds_profile_and_env(isolate):
    store_path, env_path, marker = isolate
    asker = ScriptedAsker(["Mark", "Sydney, Australia", "9am–6pm", "Short & direct"])
    answers = await onboarding.run_onboarding(
        asker, store_path=store_path, env_path=env_path
    )
    assert answers == {
        "name": "Mark",
        "location": "Sydney, Australia",
        "hours": "9am–6pm",
        "style": "Short & direct",
    }
    # profile persisted
    store = build_profile_store(store_path)
    profile = await store.read(PROFILE_PATH)
    assert "Name: Mark" in profile
    # location persisted to .env
    assert "AGCLAW_LOCATION=Sydney, Australia" in env_path.read_text()
    # marker written → won't ask again
    assert marker.exists()
    assert await onboarding.needs_onboarding(store_path) is False


async def test_run_onboarding_all_skipped_still_marks(isolate):
    store_path, env_path, marker = isolate
    asker = ScriptedAsker(["skip", "skip", "skip", "No preference"])
    answers = await onboarding.run_onboarding(
        asker, store_path=store_path, env_path=env_path
    )
    assert answers == {}
    assert not env_path.exists()  # nothing to persist
    assert marker.exists()  # but we won't pester them again


async def test_run_onboarding_partial(isolate):
    store_path, env_path, _ = isolate
    asker = ScriptedAsker(["", "Melbourne", "skip", "Detailed & thorough"])
    answers = await onboarding.run_onboarding(
        asker, store_path=store_path, env_path=env_path
    )
    assert answers == {"location": "Melbourne", "style": "Detailed & thorough"}
    assert "AGCLAW_LOCATION=Melbourne" in env_path.read_text()


async def test_run_onboarding_preserves_existing_profile(isolate):
    store_path, env_path, _ = isolate
    store = build_profile_store(store_path)
    await store.write(PROFILE_PATH, "## How they like things done\n- Existing fact.\n")
    asker = ScriptedAsker(["Mark", "skip", "skip", "No preference"])
    await onboarding.run_onboarding(asker, store_path=store_path, env_path=env_path)
    profile = await build_profile_store(store_path).read(PROFILE_PATH)
    assert "Name: Mark" in profile
    assert "Existing fact." in profile  # not clobbered
