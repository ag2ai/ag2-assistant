"""Timezone reporting at startup and in the agent's prompt.

Scheduled tasks are wall-clock local, so the process timezone decides when a task
actually fires. Container images default to UTC, which silently books reminders in the
wrong hour — the task is created and confirmed, and only the firing is wrong.

Two things make that visible, both keyed off ``config.tz_unset_in_container`` so they
cannot disagree: the startup banner, and one line in the agent's environment block asking
it to name the zone when confirming a schedule. Both are suppressed once TZ is set — a
correct clock needs no commentary, and the prompt text ships on every turn.

The container/TZ fact is resolved once at the boundary (``config.tz_unset_in_container``),
so a test states it instead of pretending to be inside Docker: the predicate itself takes
the environment and the marker file, and both readers take a Config.
"""

import os
import time
from datetime import datetime

import pytest

from assistant.agent import environment_context
from assistant.cli import local_time_banner
from assistant.config import Config, tz_unset_in_container

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


def _config(paths, *, tz_unset: bool) -> Config:
    return Config.for_paths(paths, tz_unset_in_container=tz_unset)


# --- the predicate itself ---


def test_container_without_tz_is_the_only_flagged_case(tmp_path):
    """Both halves are required: the marker file AND an empty TZ."""
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    assert tz_unset_in_container({}, marker=marker) is True
    assert tz_unset_in_container({"TZ": "Australia/Sydney"}, marker=marker) is False
    # A UTC host is an ordinary choice — no marker, no flag.
    assert tz_unset_in_container({}, marker=tmp_path / "absent") is False


def test_the_resolved_config_carries_the_fact(paths, tmp_path):
    """``resolve_config`` answers from the env it was handed, so nothing below the
    boundary re-derives it."""
    from assistant.config import resolve_config

    assert resolve_config({"TZ": "UTC"}, paths).tz_unset_in_container is False


# --- startup banner ---


def test_banner_always_reports_local_time(restore_tz):
    _set_tz("Australia/Sydney")
    out = "\n".join(local_time_banner(datetime.now().astimezone(), tz_unset=False))
    assert "Time" in out
    assert "AEST" in out or "AEDT" in out  # zone name flips with DST


def test_banner_hint_when_container_has_no_tz(restore_tz):
    _set_tz(None)
    out = "\n".join(local_time_banner(datetime.now().astimezone(), tz_unset=True))
    assert BANNER_NOTE in out
    assert "TZ=Australia/Sydney" in out  # the fix is spelled out, not just named


def test_banner_no_hint_when_the_clock_is_deliberate(restore_tz):
    """Keyed off the resolved fact, not off the zone being UTC: someone who deliberately
    set TZ=UTC (or runs a UTC host) has made the choice and shouldn't be told again."""
    _set_tz("UTC")
    out = "\n".join(local_time_banner(datetime.now().astimezone(), tz_unset=False))
    assert BANNER_NOTE not in out


def test_the_startup_banner_prints_those_lines(restore_tz, capsys):
    """The banner the CLI actually emits is the same text — the printer only echoes."""
    from assistant.cli import _echo_local_time

    _set_tz("Australia/Sydney")
    _echo_local_time()
    out = capsys.readouterr().out
    assert "Time" in out
    assert BANNER_NOTE not in out  # a host with TZ set is never nagged


# --- agent prompt ---


def test_prompt_names_timezone_when_at_risk(paths):
    assert PROMPT_LINE in environment_context(_config(paths, tz_unset=True))


def test_prompt_stays_quiet_when_the_clock_is_trustworthy(paths):
    """The whole point of making it conditional: with a correct clock, repeating the
    zone on every reply is noise the user didn't ask for."""
    assert PROMPT_LINE not in environment_context(_config(paths, tz_unset=False))


def test_prompt_still_reports_the_clock_either_way(paths):
    """Suppressing the guidance must not suppress the date/time itself."""
    for tz_unset in (True, False):
        assert "Current date and time" in environment_context(_config(paths, tz_unset=tz_unset))
