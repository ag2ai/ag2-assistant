"""The cross-repository startup regression: agent-connect's real Worker startup against
this listener (ADR 0049).

The sequence under test is agent-connect's — ``worker.preflight`` (a probe with no
notification handler and no Room Ops yet) and then ``worker.serve`` (notifier installed,
listener link dialled) — so the test lives there, in
``tests/test_cross_assistant_startup.py``, where it runs that code unmodified. This
runs that file with this interpreter and this checkout, so a change on this side that
breaks the sequence (an outbox flushed to the probe, say) fails here as well as there.
Skipped when no agent-connect checkout is reachable: a sibling ``agent-connect``
directory, or ``AGENT_CONNECT_REPO``.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
ASSISTANT_REPO = HERE.parents[1]


def _agent_connect_repo() -> Path | None:
    raw = os.environ.get("AGENT_CONNECT_REPO")
    candidates = [Path(raw)] if raw else [ASSISTANT_REPO.parent / "agent-connect"]
    for candidate in candidates:
        if (candidate / "tests" / "test_cross_assistant_startup.py").is_file():
            return candidate.resolve()
    return None


def test_agent_connects_startup_does_not_lose_a_queued_outcome():
    repo = _agent_connect_repo()
    if repo is None:
        pytest.skip("no agent-connect checkout (set AGENT_CONNECT_REPO)")
    env = {**os.environ, "AG2_ASSISTANT_REPO": str(ASSISTANT_REPO)}
    proc = subprocess.run(  # noqa: S603 - our own checkout, our own interpreter
        [sys.executable, str(repo / "tests" / "test_cross_assistant_startup.py")],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    report = proc.stdout + proc.stderr
    assert proc.returncode == 0, report
    assert "skipped" not in proc.stdout, report
    assert "cross-repo assistant startup green" in proc.stdout, report
