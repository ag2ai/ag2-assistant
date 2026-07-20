"""Manual replies typed into a run's thread must resolve the run's task, so
task-scoped folder/command grants apply to them too (spec 2026-07-20 §4)."""

import asyncio

from assistant.gateway.core import Gateway


class _Tasks:
    def __init__(self, runs):
        self.runs = runs

    async def get_run(self, run_id):
        return self.runs.get(run_id)


class _Stub:
    """Bare object carrying only what _task_for_stream touches."""

    _task_for_stream = Gateway._task_for_stream

    def __init__(self, tasks):
        self._tasks = tasks


def test_run_stream_resolves_its_task():
    stub = _Stub(_Tasks({"run-1": {"id": "run-1", "task_id": "task-9"}}))
    assert asyncio.run(stub._task_for_stream("task-run:run-1")) == "task-9"


def test_non_run_streams_and_missing_runs_resolve_empty():
    stub = _Stub(_Tasks({}))
    assert asyncio.run(stub._task_for_stream("default")) == ""
    assert asyncio.run(stub._task_for_stream("task-run:run-gone")) == ""
    stub_no_tasks = _Stub(None)
    assert asyncio.run(stub_no_tasks._task_for_stream("task-run:run-1")) == ""


def test_lookup_errors_degrade_to_plain_chat():
    class _Boom:
        async def get_run(self, run_id):
            raise RuntimeError("store down")

    assert asyncio.run(_Stub(_Boom())._task_for_stream("task-run:run-1")) == ""
