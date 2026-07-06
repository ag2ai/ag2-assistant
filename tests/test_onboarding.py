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
def isolate(tmp_path):
    """Universal store (root/user.db) + env paths in a tmp dir. There is no marker
    file — an empty universal store is the only (install-wide) onboarding gate."""
    user_store_path = tmp_path / "user.db"
    env_path = tmp_path / ".env"
    return user_store_path, env_path


def test_build_profile_renders_sections():
    md = onboarding.build_profile(
        {
            "name": "Ada",
            "location": "London, United Kingdom",
            "hours": "9am–6pm",
            "style": "Short & direct",
        }
    )
    assert "## About the user" in md
    assert "Name: Ada" in md
    assert "Location: London, United Kingdom" in md
    assert "## When they like things done" in md
    assert "9am–6pm" in md
    assert "Prefers short, direct answers." in md


def test_build_profile_empty_when_all_skipped():
    assert onboarding.build_profile({}) == ""
    assert onboarding.build_profile({"style": "No preference"}) == ""


async def test_needs_onboarding_true_when_no_profile(isolate):
    user_store_path, _ = isolate
    assert await onboarding.needs_onboarding(user_store_path) is True


async def test_needs_onboarding_false_when_profile_seeded(isolate):
    user_store_path, _ = isolate
    store = build_profile_store(user_store_path)
    await store.write(PROFILE_PATH, "## How they like things done\n- Existing fact.\n")
    assert await onboarding.needs_onboarding(user_store_path) is False


async def test_run_onboarding_seeds_profile_and_env(isolate):
    user_store_path, env_path = isolate
    asker = ScriptedAsker(["Ada", "London, United Kingdom", "9am–6pm", "Short & direct"])
    answers = await onboarding.run_onboarding(
        asker, user_store_path=user_store_path, env_path=env_path
    )
    assert answers == {
        "name": "Ada",
        "location": "London, United Kingdom",
        "hours": "9am–6pm",
        "style": "Short & direct",
    }
    # profile persisted
    store = build_profile_store(user_store_path)
    profile = await store.read(PROFILE_PATH)
    assert "Name: Ada" in profile
    # location persisted to .env
    assert "AG2ASSISTANT_LOCATION=London, United Kingdom" in env_path.read_text()
    # the seeded profile means we won't ask again (no marker file exists)
    assert await onboarding.needs_onboarding(user_store_path) is False


async def test_run_onboarding_all_skipped_leaves_profile_empty(isolate):
    user_store_path, env_path = isolate
    asker = ScriptedAsker(["skip", "skip", "skip", "No preference"])
    answers = await onboarding.run_onboarding(
        asker, user_store_path=user_store_path, env_path=env_path
    )
    assert answers == {}
    assert not env_path.exists()  # nothing to persist
    # nothing seeded → the gate is still open (would re-ask on first chat)
    assert await onboarding.needs_onboarding(user_store_path) is True


async def test_run_onboarding_partial(isolate):
    user_store_path, env_path = isolate
    asker = ScriptedAsker(["", "Melbourne", "skip", "Detailed & thorough"])
    answers = await onboarding.run_onboarding(
        asker, user_store_path=user_store_path, env_path=env_path
    )
    assert answers == {"location": "Melbourne", "style": "Detailed & thorough"}
    assert "AG2ASSISTANT_LOCATION=Melbourne" in env_path.read_text()


async def test_run_onboarding_preserves_existing_profile(isolate):
    user_store_path, env_path = isolate
    store = build_profile_store(user_store_path)
    await store.write(PROFILE_PATH, "## How they like things done\n- Existing fact.\n")
    asker = ScriptedAsker(["Ada", "skip", "skip", "No preference"])
    await onboarding.run_onboarding(asker, user_store_path=user_store_path, env_path=env_path)
    profile = await build_profile_store(user_store_path).read(PROFILE_PATH)
    assert "Name: Ada" in profile
    assert "Existing fact." in profile  # not clobbered
