"""Real artifacts on disk (or in bytes): executable stub scripts instead of patching
shutil.which, and real archives instead of a stubbed download."""

import io
import shlex
import tarfile
import time
from pathlib import Path


def write_stub(
    path: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> Path:
    """Write an executable POSIX script printing the given output and exit code."""
    lines = ["#!/bin/sh"]
    if stdout:
        lines.append(f"printf '%s\\n' {shlex.quote(stdout)}")
    if stderr:
        lines.append(f"printf '%s\\n' {shlex.quote(stderr)} >&2")
    lines.append(f"exit {exit_code}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return path


def skill_tarball(
    name: str,
    *,
    description: str,
    nested: bool = False,
    repo: str = "owner-repo-cafe123",
) -> bytes:
    """A real ``.tar.gz`` shaped like GitHub's tarball API response: one top-level
    ``<repo>-<sha>/`` directory holding the skill. ``nested`` puts it in a ``<name>/``
    subdirectory (a monorepo of skills); otherwise ``SKILL.md`` sits at the repo root
    (a standalone single-skill repo). Fed to a download route, the production extractor
    unpacks it for real."""
    body = f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n".encode()
    inner = f"{repo}/{name}/SKILL.md" if nested else f"{repo}/SKILL.md"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(inner)
        info.size = len(body)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def mcp_counter(path: Path, marker: Path) -> Path:
    """Write a JSON-RPC MCP peer retaining state and recording process lifetime."""
    path.write_text("""import atexit, json, signal, sys
from pathlib import Path
marker = Path(sys.argv[1])
def record(value):
    with marker.open("a") as file:
        file.write(value + "\\n")
record("started")
atexit.register(record, "closed")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
count = 0
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {"protocolVersion": request["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "counter", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "increment", "description": "Increment state",
                            "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        count += 1
        result = {"content": [{"type": "text", "text": str(count)}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""")
    return path


def acp_counter(path: Path) -> Path:
    """Write an ACP peer retaining prompt state within its session and recording disposal."""
    path.write_text("""import atexit, json, signal, sys
from pathlib import Path
marker = Path(sys.argv[1])
def record(value):
    with marker.open("a") as file:
        file.write(value + "\\n")
record("started")
atexit.register(record, "closed")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
count = 0
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "counter-session"}
    elif method == "session/prompt":
        with marker.with_suffix(".prompts").open("a") as file:
            file.write(json.dumps(request["params"]["prompt"]) + "\\n")
        count += 1
        print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": "counter-session", "update": {"sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": str(count)}}}}), flush=True)
        if "BLOCK_ACP" in json.dumps(request["params"]):
            continue
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""")
    return path


def write_skill(config, name="fresh-skill", description="Freshly installed capability"):
    """Write a Skill into the supplied configuration's resolved installation layer."""
    directory = config.skills_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nCurrent instructions.\n"
    )
