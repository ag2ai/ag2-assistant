"""The "Mentioned in N threads" file backlink (ADR 0014): a loose, path-historical
scan of the current profile's event store for Threads (Chats + Task Runs) whose
transcript mentions a previewed file.

Most cases drive ``Gateway.threads_mentioning`` directly (seeding transcript docs /
event logs on the store) — that's the whole behavior under test; the HTTP route is a
thin wrapper covered by ``test_mentions_endpoint_*``. Mirrors the storage-fact seams
in ``test_gateway.py`` rather than ``/files/search``'s corpus, since this scan reads
chats.db, not the filesystem."""

import json
from pathlib import Path

from ag2.knowledge.constants import LOG_PREFIX

import assistant.gateway.core as core_mod
from assistant.config import Config
from assistant.gateway.core import Gateway
from assistant.gateway.tasks_service import TaskService
from assistant.tasks.store import TaskStore
from assistant.workspace import mention_forms
from tests.support.apps import api, make_paths
from tests.support.fakes import FakeAgent


async def _gateway(tmp_path, monkeypatch, *, tasks=False) -> Gateway:
    """A started, persistent Gateway over ``tmp_path`` with a fake agent. With
    ``tasks=True`` it also owns a real TaskStore/TaskService so run rows can be
    enriched with their parent Task."""
    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: FakeAgent())
    cfg = Config.for_paths(make_paths(tmp_path), data_dir=tmp_path)
    svc = None
    if tasks:
        svc = TaskService(config=cfg, store=TaskStore(path=tmp_path / "tasks.db"))
    gw = Gateway(config=cfg, memory=False, task_service=svc)
    await gw.start()
    return gw


async def _seed_chat(gw, sid, text, *, updated="2026-01-01T00:00:00+00:00", title=None):
    """Write a display-transcript doc directly (a single user message carrying
    ``text``), so a scan sees the mention deterministically without an LLM turn."""
    doc = {
        "chat_id": sid,
        "messages": [{"role": "user", "text": text}],
        "updated": updated,
        "title": title,
    }
    await gw._event_store.write(gw._transcript_path(sid), json.dumps(doc))


async def _seed_log(gw, sid, text):
    """Write raw event-log text for a stream (no display transcript) — models a path
    that appears only in a produced/tool event, absent from the display messages."""
    await gw._event_store.write(f"{LOG_PREFIX}{sid}.jsonl", text)


# ---- ticket 01: referenced-in-a-chat, path forms, isolation, moved path ----


async def test_referenced_file_lists_its_chat(tmp_path, monkeypatch):
    """A real turn whose user message holds the file path → that chat is listed."""
    gw = await _gateway(tmp_path, monkeypatch)
    await gw.send_message("Please read /ws/report.md and summarize it", chat_id="c1")
    rows = await gw.threads_mentioning(["/ws/report.md"])
    assert [r["stream_id"] for r in rows] == ["c1"]
    assert rows[0]["kind"] == "chat"
    assert rows[0]["title"]  # falls back to the message preview when unnamed
    await gw.close()


async def test_both_path_forms_match(tmp_path, monkeypatch):
    """The OR-set matches a chat holding the absolute form AND one holding the
    workspace-relative form — a Files-space file is searched under both."""
    gw = await _gateway(tmp_path, monkeypatch)
    await _seed_chat(gw, "cabs", "opened /ws/report.md earlier")
    await _seed_chat(gw, "crel", "produced deliverables/report.md")
    rows = await gw.threads_mentioning(["/ws/report.md", "deliverables/report.md"])
    assert {r["stream_id"] for r in rows} == {"cabs", "crel"}
    await gw.close()


async def test_moved_path_returns_empty(tmp_path, monkeypatch):
    """Path-historical: the transcript froze the OLD path, so a query for the file's
    new path finds nothing (no move/rename tracking)."""
    gw = await _gateway(tmp_path, monkeypatch)
    await _seed_chat(gw, "c1", "see /ws/old-name.md")
    assert await gw.threads_mentioning(["/ws/new-name.md"]) == []
    await gw.close()


async def test_no_paths_or_empty_store_is_empty(tmp_path, monkeypatch):
    gw = await _gateway(tmp_path, monkeypatch)
    assert await gw.threads_mentioning([]) == []
    assert await gw.threads_mentioning(["/ws/whatever.md"]) == []
    await gw.close()


async def test_cross_profile_isolation(tmp_path, monkeypatch):
    """Two profiles = two stores; a mention in one is never surfaced by the other."""
    gw_a = await _gateway(tmp_path / "a", monkeypatch)
    gw_b = await _gateway(tmp_path / "b", monkeypatch)
    await _seed_chat(gw_a, "c1", "secret at /ws/secret.md")
    assert len(await gw_a.threads_mentioning(["/ws/secret.md"])) == 1
    assert await gw_b.threads_mentioning(["/ws/secret.md"]) == []
    await gw_a.close()
    await gw_b.close()


async def test_ordering_newest_first(tmp_path, monkeypatch):
    gw = await _gateway(tmp_path, monkeypatch)
    await _seed_chat(gw, "old", "/ws/f.md", updated="2026-01-01T00:00:00+00:00")
    await _seed_chat(gw, "new", "/ws/f.md", updated="2026-06-01T00:00:00+00:00")
    rows = await gw.threads_mentioning(["/ws/f.md"])
    assert [r["stream_id"] for r in rows] == ["new", "old"]
    await gw.close()


async def test_result_cap_respected(tmp_path, monkeypatch):
    gw = await _gateway(tmp_path, monkeypatch)
    for i in range(core_mod._MENTIONS_RESULT_CAP + 5):
        await _seed_chat(gw, f"c{i:03d}", "/ws/f.md")
    rows = await gw.threads_mentioning(["/ws/f.md"])
    assert len(rows) == core_mod._MENTIONS_RESULT_CAP
    await gw.close()


# ---- ticket 02: event-log corpus, Task Runs, task-page skip ----


async def test_event_log_only_mention_is_matched(tmp_path, monkeypatch):
    """A path present only in the raw event log (never the display transcript) still
    matches — the loose "mentioned anywhere" promise reads the log, not just messages."""
    gw = await _gateway(tmp_path, monkeypatch)
    await _seed_log(gw, "logonly", json.dumps({"path": "deliverables/out.md"}) + "\n")
    rows = await gw.threads_mentioning(["deliverables/out.md"])
    assert [(r["stream_id"], r["kind"]) for r in rows] == [("logonly", "chat")]
    await gw.close()


async def test_produced_file_on_run_lists_the_run(tmp_path, monkeypatch):
    """A deliverable produced by a Task Run — its path lives in a DeliverableProduced
    event on the run's log — surfaces the RUN, enriched with its parent Task name and
    run start time."""
    gw = await _gateway(tmp_path, monkeypatch, tasks=True)
    task = await gw._tasks._store.create_task("Nightly digest", "gather news")
    run = await gw._tasks._store.create_run(task.id)
    event = {"type": "DeliverableProduced", "data": {"path": "deliverables/digest.md"}}
    await _seed_log(gw, f"task-run:{run.id}", json.dumps(event) + "\n")

    rows = await gw.threads_mentioning(["deliverables/digest.md"])
    assert len(rows) == 1
    row = rows[0]
    assert row["stream_id"] == f"task-run:{run.id}"
    assert row["kind"] == "run"
    assert row["task_id"] == task.id
    assert row["task_name"] == "Nightly digest"
    assert row["run_started_at"] == run.started_at
    assert row["title"] == "Nightly digest"  # falls back to the task name
    await gw.close()


async def test_task_page_stream_is_skipped(tmp_path, monkeypatch):
    """A ``task:{id}`` page stream holds config, not a transcript — never a row, even
    if the path string happens to appear in it."""
    gw = await _gateway(tmp_path, monkeypatch)
    await _seed_log(gw, "task:t1", json.dumps({"path": "deliverables/out.md"}) + "\n")
    assert await gw.threads_mentioning(["deliverables/out.md"]) == []
    await gw.close()


async def test_dropped_segment_is_scanned(tmp_path, monkeypatch):
    """A path that only survives in a dropped-turn log segment
    (``{sid}.dropped-N.jsonl``) is still matched and attributed to the base stream."""
    gw = await _gateway(tmp_path, monkeypatch)
    await gw._event_store.write(
        f"{LOG_PREFIX}c1.dropped-1.jsonl", json.dumps({"path": "deliverables/gone.md"}) + "\n"
    )
    rows = await gw.threads_mentioning(["deliverables/gone.md"])
    assert [r["stream_id"] for r in rows] == ["c1"]
    await gw.close()


# ---- path-form helper (the endpoint's OR-set) ----


def test_mention_forms_relative_yields_both_forms(tmp_path):
    root = Path(tmp_path).resolve()
    forms = mention_forms(str(tmp_path), "reports/a.md")
    assert "reports/a.md" in forms
    assert str(root / "reports" / "a.md") in forms


def test_mention_forms_absolute_under_workspace_adds_relative(tmp_path):
    root = Path(tmp_path).resolve()
    abs_path = str(root / "reports" / "a.md")
    forms = mention_forms(str(tmp_path), abs_path)
    assert abs_path in forms
    assert "reports/a.md" in forms


def test_mention_forms_absolute_outside_workspace_is_lone(tmp_path):
    assert mention_forms(str(tmp_path), "/etc/hosts") == ["/etc/hosts"]


def test_mention_forms_blank_is_empty(tmp_path):
    assert mention_forms(str(tmp_path), "") == []
    assert mention_forms(str(tmp_path), "   ") == []


# ---- HTTP route (thin wrapper over threads_mentioning) ----


def test_mentions_endpoint_blank_path_returns_empty(profile_app):
    client, pid = profile_app
    r = client.get(api(pid, "/files/mentions"), params={"path": ""})
    assert r.status_code == 200
    assert r.json() == {"threads": []}


def test_mentions_endpoint_returns_matching_threads(profile_app):
    client, pid = profile_app
    gw = client.app.state.profiles.get(pid).gateway
    portal = client.portal
    portal.call(_seed_chat, gw, "c1", "look at reports/a.md please")
    threads = client.get(api(pid, "/files/mentions"), params={"path": "reports/a.md"}).json()[
        "threads"
    ]
    assert [t["stream_id"] for t in threads] == ["c1"]
    assert threads[0]["kind"] == "chat"
