"""The /api/coding/{agent}/models route feeding the Settings model picker.

The route drives a real ModelCatalog over the install's search path, so these
tests put real executable adapter stubs there: one that answers the ACP probe,
one that dies on it. Nothing is patched.
"""

import json

_CATALOG_REPLY = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "models": {
                "currentModelId": "codex-m",
                "availableModels": [{"modelId": "codex-m", "name": "M", "description": "d"}],
            }
        },
    }
)
_INIT_REPLY = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})


def _answering_adapter(bin_dir, name="codex-acp", marker=None):
    """An adapter stub that reads each probe request and answers it, then waits like a
    real adapter (answering blindly and exiting would close the pipe under the
    prober's next write). Optionally records each spawn in ``marker`` so a test can
    count how often it was launched."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    count = f"echo x >> {marker}\n" if marker is not None else ""
    body = "".join(
        f"read _request || exit 0\nprintf '%s\\n' {json.dumps(line)}\n"
        for line in (_INIT_REPLY, _CATALOG_REPLY)
    )
    script.write_text(f"#!/bin/sh\n{count}{body}exec cat >/dev/null\n")
    script.chmod(0o755)
    return script


def _app(profile_app_factory, bin_dir):
    """The app resolving its adapters on ``bin_dir`` and nothing else."""
    return profile_app_factory(env={"PATH": str(bin_dir)})


def test_unknown_agent_is_404(profile_app):
    client, _pid = profile_app
    r = client.get("/api/coding/gemini/models")
    assert r.status_code == 404
    assert r.json()["ok"] is False


def test_catalog_served(profile_app_factory, tmp_path):
    bin_dir = tmp_path / "bin"
    _answering_adapter(bin_dir)
    client, _pid = _app(profile_app_factory, bin_dir)
    body = client.get("/api/coding/codex/models").json()
    assert body == {
        "models": [{"id": "codex-m", "name": "M", "description": "d"}],
        "current": "codex-m",
        "reason": "",
    }


def test_missing_adapter_reports_reason_without_probing(profile_app_factory, tmp_path):
    bin_dir = tmp_path / "bin"
    marker = tmp_path / "spawns"
    _answering_adapter(bin_dir, marker=marker)  # codex is installed, claude is not
    client, _pid = _app(profile_app_factory, bin_dir)
    body = client.get("/api/coding/claude/models").json()
    # The form needs to tell the user WHY there's no list — and a probe that cannot
    # work must not be paid for at all.
    assert body == {"models": [], "current": "", "reason": "adapter_missing"}
    assert not marker.exists()  # nothing was spawned


def test_probe_failure_reads_as_probe_failed(profile_app_factory, tmp_path):
    bin_dir = tmp_path / "bin"
    # Consumes the request, then dies without answering (a stub that exits before we
    # write would break the pipe on the write side instead — a racy variant).
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "codex-acp").write_text("#!/bin/sh\nhead -n 1 >/dev/null\nexit 1\n")
    (bin_dir / "codex-acp").chmod(0o755)
    client, _pid = _app(profile_app_factory, bin_dir)
    body = client.get("/api/coding/codex/models").json()
    assert body == {"models": [], "current": "", "reason": "probe_failed"}


def test_refresh_query_param_reaches_the_catalog(profile_app_factory, tmp_path):
    bin_dir = tmp_path / "bin"
    marker = tmp_path / "spawns"
    _answering_adapter(bin_dir, marker=marker)
    client, _pid = _app(profile_app_factory, bin_dir)

    client.get("/api/coding/codex/models")
    client.get("/api/coding/codex/models")  # served from the app's TTL cache
    assert marker.read_text().count("x") == 1
    client.get("/api/coding/codex/models?refresh=1")  # bypasses it
    assert marker.read_text().count("x") == 2
