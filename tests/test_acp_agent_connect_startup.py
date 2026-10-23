"""Run the sibling agent-connect startup regression against this checkout."""

import subprocess
import sys
from pathlib import Path

import pytest

ASSISTANT_REPO = Path(__file__).resolve().parents[1]


def test_agent_connects_startup_does_not_lose_a_queued_outcome():
    repo = ASSISTANT_REPO.parent / "agent-connect"
    script = repo / "tests" / "test_cross_assistant_startup.py"
    if not script.is_file():
        pytest.skip("no sibling agent-connect checkout")
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env={"AG2_ASSISTANT_REPO": str(ASSISTANT_REPO)},
        timeout=180,
        check=False,
    )
    report = proc.stdout + proc.stderr
    assert proc.returncode == 0, report
    assert "skipped" not in proc.stdout, report
    assert "cross-repo assistant startup green" in proc.stdout, report
