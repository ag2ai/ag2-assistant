"""Timezone reporting at startup and in the agent's prompt.

Scheduled tasks are wall-clock local, so the process timezone decides when a task
actually fires. Container images default to UTC, which silently books reminders in the
wrong hour — the task is created and confirmed, and only the firing is wrong.

Two things make that visible, both keyed off ``tz_unset_in_container`` so they cannot
disagree: the startup banner, and one line in the agent's environment block asking it to
name the zone when confirming a schedule. Both are suppressed once TZ is set — a correct
clock needs no commentary, and the prompt text ships on every turn.
"""

import os
import time
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import assistant.agent as agent_mod
import assistant.cli as cli
from assistant.agent import environment_context
from assistant.config import load_config

runner = CliRunner()

BANNER_NOTE = "no TZ set"
PROMPT_LINE = "Tasks fire in this timezone"


@pytest.fixture
def restore_tz():
    """Undo per-test TZ changes: tzset() mutates process-wide state."""
    before = os.environ.get("TZ")
    yield
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


def _set_tz(value: str | None) -> None:
    if value is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = value
    time.tzset()


def _in_container(flag: bool):
    """Simulate (or rule out) running inside a container.

    Patches Path in ``assistant.agent``, where the predicate lives — the CLI imports it
    from there, so this covers the banner and the prompt in one place.
    """
    return patch.object(agent_mod.Path, "exists", return_value=flag)


def _banner() -> str:
    """Invoke `gateway` with the server stubbed out, returning its banner."""
    with patch.object(cli.uvicorn, "run"):
        return runner.invoke(cli.app, ["gateway"]).output


# --- startup banner ---


def test_banner_always_reports_local_time(restore_tz):
    _set_tz("Australia/Sydney")
    out = _banner()
    assert "Time" in out
    assert "AEST" in out or "AEDT" in out  # zone name flips with DST


def test_banner_hint_when_container_has_no_tz(restore_tz):
    _set_tz(None)
    with _in_container(True):
        out = _banner()
    assert BANNER_NOTE in out
    assert "TZ=Australia/Sydney" in out  # the fix is spelled out, not just named


def test_banner_no_hint_outside_container(restore_tz):
    """A UTC host is an ordinary choice — don't nag non-container installs."""
    _set_tz("UTC")
    with _in_container(False):
        assert BANNER_NOTE not in _banner()


def test_banner_no_hint_when_container_has_tz(restore_tz):
    """Keyed off TZ being unset, not off the zone being UTC: someone who deliberately
    set TZ=UTC has made the choice and shouldn't be told again."""
    _set_tz("UTC")
    with _in_container(True):
        assert BANNER_NOTE not in _banner()


# --- agent prompt ---


def test_prompt_names_timezone_when_at_risk(restore_tz):
    _set_tz(None)
    with _in_container(True):
        assert PROMPT_LINE in environment_context(load_config())


def test_prompt_stays_quiet_when_tz_is_set(restore_tz):
    """The whole point of making it conditional: with a correct clock, repeating the
    zone on every reply is noise the user didn't ask for."""
    _set_tz("Australia/Sydney")
    with _in_container(True):
        assert PROMPT_LINE not in environment_context(load_config())


def test_prompt_stays_quiet_outside_container(restore_tz):
    _set_tz(None)
    with _in_container(False):
        assert PROMPT_LINE not in environment_context(load_config())


def test_prompt_still_reports_the_clock_either_way(restore_tz):
    """Suppressing the guidance must not suppress the date/time itself."""
    _set_tz("Australia/Sydney")
    with _in_container(False):
        assert "Current date and time" in environment_context(load_config())
