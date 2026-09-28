"""Tests for the first-run onboarding interview."""

import os

import pytest

from assistant import onboarding
from assistant.config import read_global_config, resolve_config, update_global_section
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
    """The universal store (root/user.db) in a tmp dir. There is no marker file —
    an empty universal store is the only (install-wide) onboarding gate."""
    return tmp_path / "user.db"


def test_identity_document_renders_sections():
    md = onboarding.identity_document(
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


def test_identity_document_empty_when_all_skipped():
    assert onboarding.identity_document({}) == ""
    assert onboarding.identity_document({"name": "", "location": "  "}) == ""


def test_identity_document_freetext_style_renders_verbatim():
    """Web onboarding sends a free-text answer style (e.g. 'concise'), not one of the
    CLI's canned options — it should still render as a preference."""
    md = onboarding.identity_document({"style": "concise"})
    assert "## How they like things done" in md
    assert "Prefers answers that are concise." in md


async def test_location_lands_in_the_install_config_not_the_process_env(isolate, paths):
    """The answer is persisted where the next start reads it — config.yaml — and the
    interview never writes to os.environ."""
    user_store_path = isolate
    paths.root.mkdir(parents=True, exist_ok=True)
    update_global_section(paths, "llm", {"model": "gpt-5.5"})  # a neighbour to preserve
    asker = ScriptedAsker(["Ada", "London, United Kingdom", "skip", "skip"])

    await onboarding.run_onboarding(asker, user_store_path, paths=paths)

    doc = read_global_config(paths)
    assert doc["agent"]["location"] == "London, United Kingdom"
    assert doc["llm"] == {"model": "gpt-5.5"}
    assert resolve_config({}, paths).agent.location == "London, United Kingdom"
    assert "AG2ASSISTANT_LOCATION" not in os.environ


async def test_needs_onboarding_true_when_no_profile(isolate):
    user_store_path = isolate
    assert await onboarding.needs_onboarding(user_store_path) is True


async def test_needs_onboarding_false_when_profile_seeded(isolate):
    user_store_path = isolate
    store = build_profile_store(user_store_path)
    await store.write(PROFILE_PATH, "## How they like things done\n- Existing fact.\n")
    assert await onboarding.needs_onboarding(user_store_path) is False


async def test_run_onboarding_seeds_the_profile_and_the_install_config(isolate, paths):
    user_store_path = isolate
    asker = ScriptedAsker(["Ada", "London, United Kingdom", "9am–6pm", "Short & direct"])
    answers = await onboarding.run_onboarding(asker, user_store_path=user_store_path, paths=paths)
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
    # location persisted to the install config — the one store, and nowhere else
    assert read_global_config(paths)["agent"]["location"] == "London, United Kingdom"
    # the seeded profile means we won't ask again (no marker file exists)
    assert await onboarding.needs_onboarding(user_store_path) is False


async def test_run_onboarding_all_skipped_leaves_profile_empty(isolate, paths):
    user_store_path = isolate
    asker = ScriptedAsker(["skip", "skip", "skip", "No preference"])
    answers = await onboarding.run_onboarding(asker, user_store_path=user_store_path, paths=paths)
    assert answers == {}
    assert "agent" not in read_global_config(paths)  # nothing to persist
    # nothing seeded → the gate is still open (would re-ask on first chat)
    assert await onboarding.needs_onboarding(user_store_path) is True


async def test_run_onboarding_partial(isolate, paths):
    user_store_path = isolate
    asker = ScriptedAsker(["", "Melbourne", "skip", "Detailed & thorough"])
    answers = await onboarding.run_onboarding(asker, user_store_path=user_store_path, paths=paths)
    assert answers == {"location": "Melbourne", "style": "Detailed & thorough"}
    assert read_global_config(paths)["agent"]["location"] == "Melbourne"


async def test_run_onboarding_preserves_existing_profile(isolate, paths):
    user_store_path = isolate
    store = build_profile_store(user_store_path)
    await store.write(PROFILE_PATH, "## How they like things done\n- Existing fact.\n")
    asker = ScriptedAsker(["Ada", "skip", "skip", "No preference"])
    await onboarding.run_onboarding(asker, user_store_path=user_store_path, paths=paths)
    profile = await build_profile_store(user_store_path).read(PROFILE_PATH)
    assert "Name: Ada" in profile
    assert "Existing fact." in profile  # not clobbered
