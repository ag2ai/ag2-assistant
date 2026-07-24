"""CodingSession A2UI surface projection (assistant.coding.surface)."""

from assistant.a2ui import CATALOG_ID
from assistant.coding import surface as surfmod
from assistant.coding.diff import FileDiff
from assistant.events import A2UISurface


def _files():
    return [
        FileDiff("app.py", "modified", "@@ -1 +1 @@\n-a\n+b\n", 1, 1),
        FileDiff("new.py", "added", "@@ -0,0 +1 @@\n+x\n", 1, 0),
    ]


def test_build_surface_is_a2ui_surface_event():
    s = surfmod.build_surface(
        surface_id="cs1",
        agent_label="Claude Code",
        directory="/repo",
        task="do it",
        status="done",
        files=_files(),
    )
    assert isinstance(s, A2UISurface)
    assert s.surface_id == "cs1"
    assert s.catalog_id == CATALOG_ID
    assert s.component.get("component") == "CodingSession"


def test_root_carries_agent_directory_status():
    s = surfmod.build_surface(
        surface_id="cs1",
        agent_label="Codex",
        directory="/repo",
        task="refactor",
        status="running",
        files=[],
    )
    root = s.component
    assert root["agent"] == "Codex"
    assert root["directory"] == "/repo"
    assert root["task"] == "refactor"
    assert root["status"] == "running"


def test_files_serialized_with_hunks():
    s = surfmod.build_surface(
        surface_id="cs1",
        agent_label="Claude Code",
        directory="/repo",
        task="t",
        status="done",
        files=_files(),
    )
    files = s.component["files"]
    assert [f["path"] for f in files] == ["app.py", "new.py"]
    app = files[0]
    assert app["status"] == "modified"
    assert app["added"] == 1 and app["removed"] == 1
    assert "+b" in app["hunks"]


def test_plan_serialized():
    s = surfmod.build_surface(
        surface_id="cs1",
        agent_label="Claude Code",
        directory="/repo",
        task="t",
        status="running",
        files=[],
        plan=[{"content": "step one", "status": "in_progress"}],
    )
    assert s.component["plan"] == [{"content": "step one", "status": "in_progress"}]


def test_error_carried_when_failed():
    s = surfmod.build_surface(
        surface_id="cs1",
        agent_label="Claude Code",
        directory="/repo",
        task="t",
        status="failed",
        files=[],
        error="adapter not found",
    )
    assert s.component["status"] == "failed"
    assert s.component["error"] == "adapter not found"


def test_data_mirrors_scalar_fields():
    s = surfmod.build_surface(
        surface_id="cs1",
        agent_label="Claude Code",
        directory="/repo",
        task="t",
        status="done",
        files=_files(),
        summary="changed 2 files",
    )
    # data mirrors the component fields (generic data-only render path).
    assert s.data.get("status") == "done"
    assert s.data.get("summary") == "changed 2 files"
