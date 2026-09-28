"""Phase-2 routes answer bodies their response models accept.

Each route runs on the state that stresses its model: empty first, then with a
real task, run and inquiry. The empty pass matters because a model that made a
sometimes-absent field required would turn a green route into a 500; the
populated pass matters because the model is the contract, so a key it forgot to
declare silently disappears from the wire instead of failing loudly.
"""

import pytest

from tests.support.apps import make_paths, make_profile_app


@pytest.fixture
def client_pid(profile_app):
    return profile_app


def _make_task(client, pid, **kw):
    body = {"name": "nightly", "prompt": "do the thing", **kw}
    r = client.post(f"/api/p/{pid}/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["task"]


# ---- chats ----


def test_the_chat_list_is_empty_and_well_shaped_on_a_fresh_profile(client_pid):
    client, pid = client_pid
    r = client.get(f"/api/p/{pid}/chats")
    assert r.status_code == 200
    assert r.json() == {"chats": []}


def test_a_transcript_carries_both_models_even_for_a_chat_that_does_not_exist(client_pid):
    """The route reads through to the gateway rather than 404ing, so an unknown id
    still has to produce all four declared keys."""
    client, pid = client_pid
    body = client.get(f"/api/p/{pid}/chats/nope").json()
    assert set(body) == {"chat_id", "messages", "model", "effective_model"}
    assert body["messages"] == []


def test_patching_an_unknown_chat_404s_and_an_empty_patch_400s(client_pid):
    client, pid = client_pid
    assert client.patch(f"/api/p/{pid}/chats/nope", json={}).status_code == 400
    assert client.patch(f"/api/p/{pid}/chats/nope", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/p/{pid}/chats/nope").status_code == 404


# ---- tasks ----


def test_a_created_task_carries_every_declared_field_and_no_runs_key(client_pid):
    """POST answers _task_row, which has no `runs` — NewTaskEnvelope, not
    TaskEnvelope. A model that declared `runs` here would 500."""
    client, pid = client_pid
    task = _make_task(client, pid)
    assert set(task) == {
        "id",
        "name",
        "prompt",
        "model",
        "description",
        "schedule",
        "schedule_desc",
        "paused",
        "starred",
        "recall_depth",
        "next_run_at",
        "created_at",
        "updated_at",
        "last_run",
        "unread",
        "needs_input",
    }
    assert task["last_run"] is None
    # Declared with a default to match the zod twin's `.default(0)`, but the row
    # always carries it — the flag that would let it go missing is not set here.
    assert task["recall_depth"] == 0


def test_a_manual_schedule_keeps_its_at_and_cron_keys(client_pid):
    """The schedule editor reads `at` and `cron`. A model declaring only `kind`
    would drop them from the wire and leave the editor with nothing to load."""
    client, pid = client_pid
    task = _make_task(client, pid)
    assert task["schedule"] == {"kind": "manual", "at": None, "cron": None}


def test_a_cron_schedule_survives_the_round_trip(client_pid):
    client, pid = client_pid
    task = _make_task(client, pid, schedule={"kind": "cron", "cron": "0 9 * * *"})
    assert task["schedule"]["kind"] == "cron"
    assert task["schedule"]["cron"] == "0 9 * * *"
    assert task["schedule"]["at"] is None


def test_a_once_schedule_keeps_its_timestamp(client_pid):
    client, pid = client_pid
    task = _make_task(client, pid, schedule={"kind": "once", "at": "2030-01-01T09:00:00+00:00"})
    assert task["schedule"]["at"] == "2030-01-01T09:00:00+00:00"
    assert task["schedule"]["cron"] is None


def test_getting_a_task_adds_the_run_history(client_pid):
    client, pid = client_pid
    created = _make_task(client, pid)
    body = client.get(f"/api/p/{pid}/tasks/{created['id']}").json()
    assert body["task"]["runs"] == []
    assert body["task"]["id"] == created["id"]


def test_patching_a_task_answers_the_same_envelope_as_the_get(client_pid):
    """update_task returns get_task, so the body carries `runs` — a model without
    it would strip the history the task page renders."""
    client, pid = client_pid
    created = _make_task(client, pid)
    body = client.patch(f"/api/p/{pid}/tasks/{created['id']}", json={"name": "renamed"}).json()
    assert body["task"]["name"] == "renamed"
    assert body["task"]["runs"] == []


def test_the_task_list_is_empty_then_carries_the_row(client_pid):
    client, pid = client_pid
    assert client.get(f"/api/p/{pid}/tasks").json() == {"tasks": []}
    created = _make_task(client, pid)
    assert [t["id"] for t in client.get(f"/api/p/{pid}/tasks").json()["tasks"]] == [created["id"]]


def test_the_error_branches_answer_their_own_bodies_not_the_model(client_pid):
    """A bad schedule is a 422 {error}, an unknown task a bare 404 — both return a
    Response, which bypasses response_model entirely."""
    client, pid = client_pid
    bad = client.post(f"/api/p/{pid}/tasks", json={"prompt": "x", "schedule": {"kind": "weekly"}})
    assert bad.status_code == 422
    assert "error" in bad.json()
    assert client.get(f"/api/p/{pid}/tasks/nope").status_code == 404
    assert client.delete(f"/api/p/{pid}/tasks/nope").status_code == 404
    assert client.patch(f"/api/p/{pid}/tasks/nope", json={"name": "x"}).status_code == 404


# ---- runs ----


def test_a_started_run_answers_a_detail_carrying_its_task_name(client_pid):
    """get_run stamps task_name onto the view; RunDetail declares it, plain Run
    does not, so the two envelopes are genuinely different models."""
    client, pid = client_pid
    created = _make_task(client, pid)
    run = client.post(f"/api/p/{pid}/tasks/{created['id']}/run").json()["run"]
    assert run["task_name"] == created["name"]
    assert set(run) == {
        "id",
        "task_id",
        "status",
        "trigger",
        "started_at",
        "ended_at",
        "summary",
        "error",
        "seen",
        "task_name",
    }
    fetched = client.get(f"/api/p/{pid}/runs/{run['id']}").json()["run"]
    assert fetched["id"] == run["id"]


def test_the_run_history_omits_the_task_name(client_pid):
    """GET /tasks/{id}/runs answers task["runs"], built by _run_view without the
    task_name get_run adds. Declaring it here would 500."""
    client, pid = client_pid
    created = _make_task(client, pid)
    client.post(f"/api/p/{pid}/tasks/{created['id']}/run")
    runs = client.get(f"/api/p/{pid}/tasks/{created['id']}/runs").json()["runs"]
    assert runs and "task_name" not in runs[0]


def test_stop_and_seen_answer_a_bare_ok_for_an_unknown_run(client_pid):
    client, pid = client_pid
    assert client.post(f"/api/p/{pid}/runs/nope/stop").json() == {"ok": False}
    assert client.post(f"/api/p/{pid}/runs/nope/seen").json() == {"ok": False}
    assert client.get(f"/api/p/{pid}/runs/nope").status_code == 404


# ---- inquiries and task-scoped permissions ----


def test_pending_inquiries_is_an_empty_envelope_when_nothing_is_asked(client_pid):
    client, pid = client_pid
    assert client.get(f"/api/p/{pid}/inquiries/pending").json() == {"pending": []}


def test_answering_an_unknown_inquiry_404s(client_pid):
    client, pid = client_pid
    r = client.post(f"/api/p/{pid}/inquiries/nope/answer", json={"answer": "yes"})
    assert r.status_code == 404


def test_a_tasks_own_rules_are_an_empty_list_not_the_global_set(client_pid):
    client, pid = client_pid
    created = _make_task(client, pid)
    r = client.get(f"/api/p/{pid}/tasks/{created['id']}/permissions")
    assert r.status_code == 200
    assert r.json() == {"rules": []}


def test_revoking_a_rule_a_task_never_had_is_a_plain_false(client_pid):
    client, pid = client_pid
    created = _make_task(client, pid)
    r = client.request(
        "DELETE",
        f"/api/p/{pid}/tasks/{created['id']}/permissions",
        json={"rule": "run_code"},
    )
    assert r.json() == {"ok": False}


def test_the_permission_routes_404_on_an_unknown_task(client_pid):
    client, pid = client_pid
    assert client.get(f"/api/p/{pid}/tasks/nope/permissions").status_code == 404
    r = client.request("DELETE", f"/api/p/{pid}/tasks/nope/permissions", json={"rule": "run_code"})
    assert r.status_code == 404


# ---- the message route ----


def test_a_sent_message_answers_the_reply_and_the_chat_it_landed_in(tmp_path):
    """The one phase-2 route that already had a response_model. Built here rather
    than on the shared fixture so the faked agent's reply is the assertion."""
    from fastapi.testclient import TestClient

    app, pid = make_profile_app(make_paths(tmp_path), persist=True)
    with TestClient(app) as client:
        body = client.post(f"/api/p/{pid}/message", json={"text": "hi", "chat_id": "c1"}).json()
    assert set(body) == {"reply", "chat_id"}
    assert body["chat_id"] == "c1"
