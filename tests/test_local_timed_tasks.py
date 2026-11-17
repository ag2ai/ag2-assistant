"""Hosts can disable native timed tasks while keeping on-demand task execution."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from assistant.activity import Activity
from assistant.agent import universal_turn_prompt
from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.profiles import ProfileRegistry
from assistant.tasks.store import TaskStore
from tests.support.apps import api, make_manager, make_profile_app
from tests.support.fakes import (
    FakeGateway,
    ScriptedModels,
    failing_agent_factory,
    fake_summary_factory,
)


@pytest.mark.parametrize(
    "schedule",
    [{"kind": "once", "at": "2099-01-01T00:00:00Z"}, {"kind": "cron", "cron": "@daily"}],
)
async def test_disabled_timed_tasks_preserve_legacy_and_manual_runs(paths, tmp_path, schedule):
    store = TaskStore(path=tmp_path / "tasks.db")
    legacy = await store.create_task(name="Legacy", prompt="legacy prompt", schedule=schedule)
    paused = await store.create_task(name="Paused", prompt="p", schedule=schedule)
    await store.update_task(paused.id, paused=True)
    activity = Activity()
    activity.set_scheduled_unknown(Config.for_paths(paths).data_dir.name)
    gateway = FakeGateway(hang=True)
    service = TaskService(
        config=Config.for_paths(paths),
        store=store,
        local_timed_tasks=False,
        activity=activity,
        summary_factory=fake_summary_factory(),
    )
    service.set_gateway(gateway)
    await service.start()
    try:
        assert not service.scheduler_running
        assert activity.holds() == []
        assert (await service.get_task(legacy.id))["schedule"] == schedule
        with pytest.raises(ValueError, match="Local timed tasks are disabled"):
            await service.create_task(name="Timed", prompt="p", schedule=schedule)
        manual = await service.create_task(name="Manual", prompt="manual prompt")
        for task_id, patch in [
            (manual["id"], {"schedule": schedule}),
            (paused.id, {"paused": False}),
            (legacy.id, {"next_run_at": "2099-01-01T00:00:00Z"}),
        ]:
            with pytest.raises(ValueError, match="Local timed tasks are disabled"):
                await service.update_task(task_id, **patch)
        await service._fire(legacy.id)
        for trigger in ("schedule", "once"):
            with pytest.raises(ValueError, match="Local timed tasks are disabled"):
                await service.start_run(legacy.id, trigger=trigger)
        assert await store.list_runs() == []
        assert (await store.get_task(legacy.id)).to_dict() == legacy.to_dict()
        assert (await store.get_task(paused.id)).paused
        await service.update_task(legacy.id, description="Still readable", paused=True)
        await service.update_task(legacy.id, schedule={"kind": "manual"}, paused=False)
        run = await service.start_run(manual["id"])
        assert activity.busy
        gateway._gate.set()
        await asyncio.wait_for(service._jobs_done(), 5)
        assert (await service.get_run(run.id))["status"] == "completed"
        assert gateway.sent[0]["text"] == "manual prompt"
        assert activity.holds() == []
        assert await service.delete_task(paused.id)
    finally:
        await service.close()
    assert activity.holds() == []


async def test_disabled_policy_reaches_every_profile_including_create_restore_and_failed_boot(
    paths,
):
    registry = ProfileRegistry(paths)
    owner = registry.create_profile("Owner", "#109e91")
    team = registry.create_profile("Team", "#109e91")
    broken = registry.create_profile("Broken", "#109e91")
    store = TaskStore(path=paths.profile_dir(team.id) / "tasks.db")
    await store.create_task(name="Old", prompt="p", schedule={"kind": "cron", "cron": "@daily"})
    manager = make_manager(
        paths, local_timed_tasks=False, agent_factory=failing_agent_factory({broken.id})
    )
    await manager.start()
    try:
        assert broken.id in manager.boot_errors
        assert manager.activity.holds() == []
        created = await manager.create("New", "#109e91")
        await manager.archive(team.id)
        restored = await manager.restore(team.id)
        for runtime in (manager.get(owner.id), created, restored):
            assert not runtime.require_tasks().scheduler_running
            with pytest.raises(ValueError, match="Local timed tasks are disabled"):
                await runtime.require_tasks().create_task(
                    name="No", prompt="p", schedule={"kind": "cron", "cron": "@daily"}
                )
        assert manager.activity.holds() == []
    finally:
        await manager.close()


def test_disabled_rest_create_update_and_manual_run(paths):
    app, pid = make_profile_app(paths, local_timed_tasks=False)
    with TestClient(app) as client:
        body = {"name": "Timed", "prompt": "p", "schedule": {"kind": "cron", "cron": "@daily"}}
        response = client.post(api(pid, "/tasks"), json=body)
        assert response.status_code == 422
        assert response.json() == {"error": "Local timed tasks are disabled"}
        manual = client.post(api(pid, "/tasks"), json={"name": "Manual", "prompt": "p"})
        assert manual.status_code == 200
        task_id = manual.json()["task"]["id"]
        response = client.patch(api(pid, f"/tasks/{task_id}"), json={"schedule": body["schedule"]})
        assert response.status_code == 422
        assert client.post(api(pid, f"/tasks/{task_id}/run")).status_code == 200


def test_disabled_prompt_does_not_offer_native_scheduling(paths):
    config = Config.for_paths(paths)
    enabled = "\n".join(universal_turn_prompt(config, google=False))
    disabled = "\n".join(universal_turn_prompt(config, google=False, local_timed_tasks=False))
    assert "standard 5-field cron" in enabled
    assert "standard 5-field cron" not in disabled
    assert "reschedule" not in disabled
    assert "Local timed tasks are disabled" in disabled


async def test_disabled_gateway_turn_uses_manual_task_guidance(paths):
    models = ScriptedModels()
    profile = ProfileRegistry(paths).create_profile("Cloud", "#109e91")
    manager = make_manager(paths, model_factory=models, local_timed_tasks=False)
    await manager.start()
    try:
        await manager.get(profile.id).require_gateway().send_message("What can you do?")
        prompt = "\n".join(models.requests[-1][1])
        assert "Local timed tasks are disabled" in prompt
        assert "standard 5-field cron" not in prompt
        assert "run something on a schedule" not in prompt
    finally:
        await manager.close()
