"""REST task surface: the service calls the routes lean on, plus app import,
plus HTTP-level coverage of the routes themselves (status codes / bodies)."""

import pytest
from fastapi.testclient import TestClient

from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.tasks.store import TaskStore
from tests.conftest import api, make_profile_app, use_fake_agent


def test_app_imports_cleanly():
    import assistant.gateway.app  # noqa: F401  (route wiring is executed at import)


async def test_update_task_patch_semantics(tmp_path):
    svc = TaskService(
        config=Config(),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
    )
    t = await svc.create_task(name="A", prompt="p")
    # partial patch: only what's sent changes
    out = await svc.update_task(t["id"], name="B")
    assert out["name"] == "B" and out["prompt"] == "p"
    out = await svc.update_task(t["id"], schedule={"kind": "cron", "cron": "@daily"}, paused=True)
    assert out["paused"] is True and out["schedule"]["cron"] == "0 0 * * *"
    with pytest.raises(ValueError):
        await svc.update_task(t["id"], schedule={"kind": "cron", "cron": "nope"})
    assert await svc.update_task("task-missing", name="X") is None


# ---- HTTP-level: /api/p/{pid}/tasks* and /runs* ----


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def test_create_task_http_returns_200_with_task_body(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        r = client.post(api(pid, "/tasks"), json={"name": "Digest", "prompt": "collect news"})
        assert r.status_code == 200, r.text
        task = r.json()["task"]
        assert task["name"] == "Digest" and task["prompt"] == "collect news"
        assert task["model"] is None and task["id"]


def test_create_task_http_bad_cron_is_422(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        r = client.post(
            api(pid, "/tasks"),
            json={"name": "Bad", "prompt": "p", "schedule": {"kind": "cron", "cron": "junk"}},
        )
        assert r.status_code == 422, r.text
        assert "error" in r.json()


def test_get_task_http_missing_is_404(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        assert client.get(api(pid, "/tasks/task-missing")).status_code == 404


def test_patch_task_http_model_empty_clears_to_default(monkeypatch):
    from assistant import llm_configs

    cfg = llm_configs.save_config({"name": "Claude", "type": "anthropic", "model": "cl"})
    client, pid = _client(monkeypatch)
    with client:
        created = client.post(api(pid, "/tasks"), json={"name": "M", "prompt": "p"}).json()["task"]
        tid = created["id"]
        r = client.patch(api(pid, f"/tasks/{tid}"), json={"model": cfg["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["task"]["model"] == cfg["id"]

        # "" is the sentinel that clears the model back to the profile default —
        # a bare null patch would be dropped as "unset" and silently no-op instead.
        r = client.patch(api(pid, f"/tasks/{tid}"), json={"model": ""})
        assert r.status_code == 200, r.text
        assert r.json()["task"]["model"] is None


def test_patch_task_http_bad_schedule_is_422(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        created = client.post(api(pid, "/tasks"), json={"name": "S", "prompt": "p"}).json()["task"]
        r = client.patch(
            api(pid, f"/tasks/{created['id']}"),
            json={"schedule": {"kind": "cron", "cron": "junk"}},
        )
        assert r.status_code == 422, r.text
        assert "error" in r.json()


def test_run_stop_and_seen_http_wiring(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        created = client.post(api(pid, "/tasks"), json={"name": "R", "prompt": "go"}).json()["task"]
        r = client.post(api(pid, f"/tasks/{created['id']}/run"))
        assert r.status_code == 200, r.text
        run_id = r.json()["run"]["id"]
        assert run_id

        # Timing against the background turn isn't pinned down here — only that
        # the routes dispatch to TaskService and hand back its {"ok": bool}.
        stop = client.post(api(pid, f"/runs/{run_id}/stop"))
        assert stop.status_code == 200 and isinstance(stop.json()["ok"], bool)

        seen = client.post(api(pid, f"/runs/{run_id}/seen"))
        assert seen.status_code == 200 and seen.json()["ok"] is True
