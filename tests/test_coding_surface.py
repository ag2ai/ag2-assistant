"""The CodingSession Card, filled by the server (assistant.coding.surface).

The run's state is a Card instance like any other — what is asserted here is the
fields the server fills, not the primitives they are drawn as; that drawing is the
Card file's and is covered in ``test_a2ui``.
"""

from assistant.a2ui import CATALOG_ID, CardCatalog, bundled_cards, expanded_card_surface
from assistant.coding import surface as surfmod
from assistant.coding.diff import FileDiff
from assistant.events import A2UISurface, A2UISurfaceDataUpdated
from assistant.gateway.wire import as_drawn


def _files():
    return [
        FileDiff("app.py", "modified", "@@ -1 +1 @@\n-a\n+b\n", 1, 1),
        FileDiff("new.py", "added", "@@ -0,0 +1 @@\n+x\n", 1, 0),
    ]


def _state(**over):
    state = {
        "agent_label": "Claude Code",
        "directory": "/repo",
        "task": "do it",
        "status": "done",
        "files": _files(),
    }
    return {**state, **over}


def test_the_first_emit_is_a_card_instance_on_a_surface():
    s = surfmod.build_surface("cs1", surfmod.card_fields(**_state()))
    assert isinstance(s, A2UISurface)
    assert s.surface_id == "cs1"
    assert s.catalog_id == CATALOG_ID
    assert s.component.get("component") == "CodingSession"
    # The Card is in the catalog, so the instance draws as primitives on the way out.
    assert "CodingSession" in bundled_cards()
    assert expanded_card_surface(s, bundled_cards()).component["component"] == "Card"


def test_the_run_names_its_agent_folder_and_state():
    fields = surfmod.card_fields(**_state(agent_label="Codex", status="running", files=[]))
    assert fields["agent"] == "Codex"
    assert fields["directory"] == "/repo"
    assert fields["status"] == "running"


def test_a_long_task_is_a_headline_and_the_rest_of_it():
    task = "Add a /health endpoint. Then wire it into the router and cover it with a test."
    fields = surfmod.card_fields(**_state(task=task))
    assert fields["title"] == "Add a /health endpoint."
    # The brief says what the headline did not, rather than repeating it first.
    assert fields["brief"] == "Then wire it into the router and cover it with a test."


def test_a_task_that_is_already_one_line_has_no_brief_under_it():
    fields = surfmod.card_fields(**_state(task="Add a /health endpoint"))
    assert fields["title"] == "Add a /health endpoint"
    assert "brief" not in fields


def test_a_headline_too_long_for_a_headline_is_cut_at_a_word():
    task = "Rewrite " + "the billing module and " * 8 + "ship it"
    fields = surfmod.card_fields(**_state(task=task))
    assert len(fields["title"]) <= 96
    assert fields["title"].endswith("…")
    assert not fields["title"].removesuffix("…").endswith(" ")
    # Nothing the headline showed is shown again: the brief picks the task back up
    # from where the cut fell, so a long prompt is printed once.
    assert fields["title"].removesuffix("…") + " " + fields["brief"] == task


def test_a_headline_with_no_word_to_break_at_is_cut_anyway():
    fields = surfmod.card_fields(**_state(task="x" * 200))
    assert len(fields["title"]) == 96
    assert fields["brief"] == "x" * 105


def test_each_changed_file_carries_its_diff_its_counts_and_a_path_to_open():
    fields = surfmod.card_fields(**_state())
    assert [f["path"] for f in fields["files"]] == ["app.py", "new.py"]
    app = fields["files"][0]
    assert app["status"] == "modified"
    assert app["full"] == "/repo/app.py"
    assert app["added"] == "+1" and app["removed"] == "−1"
    assert "+b" in app["hunks"]


def test_a_file_with_no_diff_says_why_instead():
    fields = surfmod.card_fields(**_state(files=[FileDiff("logo.png", "added", "", 0, 0)]))
    entry = fields["files"][0]
    assert "hunks" not in entry
    assert "preview" in entry["note"].lower()


def test_the_diff_is_totalled_across_the_run():
    fields = surfmod.card_fields(**_state())
    assert fields["changed"] == "2 files changed"
    assert fields["added"] == "+2" and fields["removed"] == "−1"


def test_one_file_is_not_two():
    fields = surfmod.card_fields(**_state(files=_files()[:1]))
    assert fields["changed"] == "1 file changed"


def test_a_run_that_changed_nothing_says_so():
    fields = surfmod.card_fields(**_state(files=[]))
    assert fields["note"] == "No file changes were made."
    assert "changed" not in fields


def test_a_run_with_no_plan_yet_says_it_is_warming_up():
    fields = surfmod.card_fields(**_state(status="running", files=[]))
    assert "warming" in fields["note"].lower()


def test_a_plan_replaces_the_warming_line():
    fields = surfmod.card_fields(
        **_state(
            status="running", files=[], plan=[{"content": "step one", "status": "in_progress"}]
        )
    )
    assert fields["plan"] == [{"content": "step one", "status": "in_progress"}]
    assert "note" not in fields


def test_a_failed_run_carries_its_error():
    fields = surfmod.card_fields(**_state(status="failed", files=[], error="adapter not found"))
    assert fields["status"] == "failed"
    assert fields["error"] == "adapter not found"


def test_a_later_emit_is_data_only_so_the_layout_is_not_re_sent():
    fields = surfmod.card_fields(**_state(summary="changed 2 files"))
    update = surfmod.surface_data("cs1", fields)
    assert isinstance(update, A2UISurfaceDataUpdated)
    assert update.surface_id == "cs1"
    assert update.data["status"] == "done"
    assert update.data["summary"] == "changed 2 files"
    # Same fields as the first emit carried, so nothing about the Card is re-sent.
    assert update.data == surfmod.build_surface("cs1", fields).data


def _stored_before_the_card_was_a_file() -> A2UISurface:
    """A coding run as a thread persisted it when the session had its own renderer."""
    root = {
        "id": "root",
        "component": "CodingSession",
        "agent": "Claude Code",
        "directory": "/repo",
        "task": "Add a /health endpoint. Then cover it with a test.",
        "status": "done",
        "plan": [],
        "files": [
            {"path": "app.py", "status": "modified", "added": 3, "removed": 1, "hunks": "@@"}
        ],
        "summary": "Done.",
    }
    data = {key: value for key, value in root.items() if key not in ("id", "component")}
    return A2UISurface(
        "cs1",
        catalog_id=CATALOG_ID,
        version="v1.0",
        component={**root, "_components": [root]},
        data=data,
        title="Coding session",
        intent="generated-ui",
    )


def test_a_run_stored_before_the_card_was_a_file_draws_its_headline_and_its_files(config):
    drawn = as_drawn(_stored_before_the_card_was_a_file(), CardCatalog(config))

    assert drawn.component["component"] == "Card"
    assert drawn.data["title"] == "Add a /health endpoint."
    assert drawn.data["brief"] == "Then cover it with a test."
    assert drawn.data["files"][0]["full"] == "/repo/app.py"
    assert (drawn.data["added"], drawn.data["removed"]) == ("+3", "−1")
    assert drawn.data["summary"] == "Done."


def test_a_redrawn_run_keeps_the_moment_it_was_recorded(config):
    stored = _stored_before_the_card_was_a_file()
    stored.created_at = 1_758_555_420.0

    assert as_drawn(stored, CardCatalog(config)).created_at == 1_758_555_420.0

    current = surfmod.build_surface("cs2", surfmod.card_fields(**_state()))
    current.created_at = 1_758_555_420.0
    assert as_drawn(current, CardCatalog(config)).created_at == 1_758_555_420.0
