"""End-to-end coding run against a real host ACP adapter.

Marked ``integration`` (excluded from the default run): it spawns the real
``claude-agent-acp`` adapter, which needs it installed on PATH and an on-disk
Claude login, and makes a real model call. Run with: ``pytest -m integration``.
"""

import shutil
import subprocess

import pytest
from ag2.context import ConversationContext
from ag2.events import ModelMessageChunk
from ag2.stream import MemoryStream

from assistant.coding import config as cfgmod
from assistant.coding import diff, session
from assistant.coding.detect import AgentInfo

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    shutil.which("claude-agent-acp") is None, reason="claude-agent-acp not installed"
)
async def test_real_claude_agent_edits_and_surfaces(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    live: list = []

    async def collect(event):
        live.append(event)

    stream = MemoryStream(id="itest")
    stream.subscribe(collect)
    context = ConversationContext(stream=stream)

    info = AgentInfo(
        "claude", "Claude Code", ["claude-agent-acp"], True, shutil.which("claude-agent-acp")
    )
    # auto-approve so the run is hands-free (no human on the HITL channel here)
    config = cfgmod.build_config(info, str(tmp_path), permission_policy="auto")

    baseline = diff.capture(str(tmp_path))
    try:
        reply = await session._default_runner(
            config, "Create a file named hello.txt with exactly: hi", context
        )
    except Exception as exc:  # noqa: BLE001
        if "auth" in str(exc).lower():
            pytest.skip(f"claude-agent-acp not authenticated in this environment: {exc}")
        raise

    # live ACP events surfaced onto the shared stream
    assert any(isinstance(e, ModelMessageChunk) for e in live)
    # the working-tree diff captured the new file
    files = diff.compute_diff(baseline, str(tmp_path))
    assert any(f.path == "hello.txt" and f.status == "added" for f in files)
    assert (tmp_path / "hello.txt").exists()
    assert isinstance(reply, str)
