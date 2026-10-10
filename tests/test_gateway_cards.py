"""Cards Settings and mounted files through the gateway's public API."""

import json

import pytest
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from tests.support.apps import api, make_manager, turn_catalog
from tests.support.cards import write_card
from tests.support.fakes import ScriptedModels


def test_cards_list_shared_files_and_their_layer(profile_app, paths):
    client, pid = profile_app
    global_file = write_card(paths.cards_dir, "Shelf")
    own_file = write_card(paths.profile_dir(pid) / "workspace" / "cards", "Private")
    response = client.get("/api/cards")
    assert response.status_code == 200
    rows = {c["name"]: c for c in response.json()["cards"]}
    assert rows["Shelf"]["origin"] == "global"
    assert rows["Shelf"]["path"] == str(global_file)
    assert rows["Shelf"]["enabled"] is True
    assert rows["WeatherPanel"]["origin"] == "bundled"
    assert "Private" not in rows
    own = client.get(api(pid, "/cards"))
    assert own.status_code == 200
    private = next(c for c in own.json()["cards"] if c["name"] == "Private")
    assert private["path"] == "cards/" + own_file.name
    assert private["available"] is True


def test_settings_reports_broken_and_duplicate_files(profile_app, paths):
    client, pid = profile_app
    first = write_card(paths.cards_dir, "Shelf")
    duplicate = paths.cards_dir / "z-copy.card.yaml"
    duplicate.write_text(first.read_text())
    broken = paths.cards_dir / "broken.card.yaml"
    broken.write_text("name: [")
    response = client.get("/api/cards")
    assert response.status_code == 200
    problems = {p["path"]: p["error"] for p in response.json()["problems"]}
    assert "unreadable" in problems[str(broken)]
    assert "already names Shelf" in problems[str(duplicate)]
    assert {str(first), str(broken), str(duplicate)} <= {
        f["path"] for f in response.json()["files"]
    }


def test_shared_switch_reaches_the_next_turn_of_existing_chats(paths):
    write_card(paths.cards_dir, "Shelf", topic="books on my shelf")
    models = ScriptedModels()
    with TestClient(create_app(make_manager(paths, model_factory=models))) as client:
        for name in ("Work", "Home"):
            assert (
                client.post("/api/profiles", json={"name": name, "accent": "#109e91"}).status_code
                == 200
            )
        for pid in ("work", "home"):
            assert "books on my shelf" in turn_catalog(client, models, pid)
        off = client.post("/api/cards/state?name=Shelf", json={"enabled": False})
        assert off.status_code == 200
        assert not next(c for c in off.json()["cards"] if c["name"] == "Shelf")["enabled"]
        for pid in ("work", "home"):
            assert "books on my shelf" not in turn_catalog(client, models, pid)
        assert client.post("/api/cards/state?name=Shelf", json={"enabled": True}).status_code == 200
        assert "books on my shelf" in turn_catalog(client, models, "work")


def test_suppression_is_scoped_to_one_profile_and_cannot_target_its_own_copy(profile_app, paths):
    client, pid = profile_app
    write_card(paths.cards_dir, "Shelf")
    other = client.post("/api/profiles", json={"name": "Other", "accent": "#109e91"}).json()[
        "profile"
    ]["id"]
    off = client.post(api(pid, "/cards/suppress?name=Shelf"))
    assert off.status_code == 200
    assert not next(c for c in off.json()["cards"] if c["name"] == "Shelf")["available"]
    assert next(
        c for c in client.get(api(other, "/cards")).json()["cards"] if c["name"] == "Shelf"
    )["available"]
    assert client.delete(api(pid, "/cards/suppress?name=Shelf")).status_code == 200
    write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    assert client.post(api(pid, "/cards/suppress?name=Shelf")).status_code == 409


def test_profile_card_disable_is_independent_of_same_named_shared_card(profile_app, paths):
    client, pid = profile_app
    write_card(paths.cards_dir, "Shelf")
    write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    response = client.post(api(pid, "/cards/state?name=Shelf"), json={"enabled": False})
    assert response.status_code == 200
    own = next(c for c in response.json()["cards"] if c["name"] == "Shelf")
    assert own["origin"] == "profile" and not own["available"]
    assert next(c for c in client.get("/api/cards").json()["cards"] if c["name"] == "Shelf")[
        "enabled"
    ]
    assert (
        client.post(api(pid, "/cards/state?name=WeatherPanel"), json={"enabled": False}).status_code
        == 404
    )


def test_global_delete_clears_shared_records_and_preserves_profile_disable(profile_app, paths):
    client, pid = profile_app
    global_file = write_card(paths.cards_dir, "Shelf")
    global_file.rename(paths.cards_dir / "unrelated-filename.card.yaml")
    client.post("/api/cards/state?name=Shelf", json={"enabled": False})
    client.post(api(pid, "/cards/suppress?name=Shelf"))
    own = write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    client.post(api(pid, "/cards/state?name=Shelf"), json={"enabled": False})
    deleted = client.delete("/api/cards?name=Shelf")
    assert deleted.status_code == 200
    assert "Shelf" not in {c["name"] for c in deleted.json()["cards"]}
    assert (
        client.get(
            api(pid, "/files/raw"),
            params={"path": str(global_file.parent / "unrelated-filename.card.yaml")},
        ).status_code
        == 404
    )
    write_card(paths.cards_dir, "Shelf")
    assert next(c for c in client.get("/api/cards").json()["cards"] if c["name"] == "Shelf")[
        "enabled"
    ]
    assert not next(
        c for c in client.get(api(pid, "/cards")).json()["cards"] if c["name"] == "Shelf"
    )["available"]
    own.unlink()
    inherited = next(
        c for c in client.get(api(pid, "/cards")).json()["cards"] if c["name"] == "Shelf"
    )
    assert inherited["available"] and not inherited["suppressed"]
    assert client.delete("/api/cards?name=WeatherPanel").status_code == 409


def test_profile_delete_resets_only_its_own_record_and_reveals_suppressed_shared_card(
    profile_app, paths
):
    client, pid = profile_app
    write_card(paths.cards_dir, "Shelf")
    client.post(api(pid, "/cards/suppress?name=Shelf"))
    write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    client.post(api(pid, "/cards/state?name=Shelf"), json={"enabled": False})
    deleted = client.delete(api(pid, "/cards?name=Shelf"))
    assert deleted.status_code == 200
    inherited = next(c for c in deleted.json()["cards"] if c["name"] == "Shelf")
    assert inherited["origin"] == "global" and inherited["suppressed"]
    write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    own = next(c for c in client.get(api(pid, "/cards")).json()["cards"] if c["name"] == "Shelf")
    assert own["available"]
    assert client.delete(api(pid, "/cards?name=WeatherPanel")).status_code == 409


def test_mounted_global_card_uses_files_save_conflicts_and_can_repair_a_broken_file(
    profile_app, paths
):
    client, pid = profile_app
    card = write_card(paths.cards_dir, "Shelf")
    raw = api(pid, "/files/raw")
    opened = client.get(raw, params={"path": str(card)})
    assert opened.status_code == 200
    assert opened.headers["X-File-Mode"] == "read_write"
    original = opened.text
    saved = client.put(
        raw,
        params={"path": str(card)},
        content=original.replace("Shelf", "Books"),
        headers={"If-Match": opened.headers["ETag"]},
    )
    assert saved.status_code == 200
    assert "Books" in {c["name"] for c in client.get("/api/cards").json()["cards"]}
    conflict = client.put(
        raw,
        params={"path": str(card)},
        content=original,
        headers={"If-Match": opened.headers["ETag"]},
    )
    assert conflict.status_code == 409
    broken = paths.cards_dir / "broken.card.yaml"
    broken.write_text("name: [")
    bad_open = client.get(raw, params={"path": str(broken)})
    assert bad_open.status_code == 200
    repaired = client.put(
        raw,
        params={"path": str(broken)},
        content=original,
        headers={"If-Match": bad_open.headers["ETag"]},
    )
    assert repaired.status_code == 200
    assert not client.get("/api/cards").json()["problems"]


def test_files_delete_resets_card_state_in_its_own_scope(profile_app, paths):
    client, pid = profile_app
    global_file = write_card(paths.cards_dir, "Shelf")
    client.post("/api/cards/state?name=Shelf", json={"enabled": False})
    client.post(api(pid, "/cards/suppress?name=Shelf"))
    assert (
        client.delete(api(pid, "/files/raw"), params={"path": str(global_file)}).status_code == 200
    )
    write_card(paths.cards_dir, "Shelf")
    inherited = next(
        c for c in client.get(api(pid, "/cards")).json()["cards"] if c["name"] == "Shelf"
    )
    assert inherited["available"]
    client.post(api(pid, "/cards/suppress?name=Shelf"))
    write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    client.post(api(pid, "/cards/state?name=Shelf"), json={"enabled": False})
    assert client.delete(api(pid, "/files/raw"), params={"path": "cards"}).status_code == 200
    inherited = next(
        c for c in client.get(api(pid, "/cards")).json()["cards"] if c["name"] == "Shelf"
    )
    assert inherited["suppressed"]
    write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    assert next(c for c in client.get(api(pid, "/cards")).json()["cards"] if c["name"] == "Shelf")[
        "available"
    ]


def test_shared_mounts_keep_bundled_read_only_and_do_not_expose_other_root_files(
    profile_app, paths
):
    client, pid = profile_app
    raw = api(pid, "/files/raw")
    bundled = next(
        c for c in client.get("/api/cards").json()["cards"] if c["name"] == "WeatherPanel"
    )["path"]
    opened = client.get(raw, params={"path": bundled})
    assert opened.status_code == 200 and opened.headers["X-File-Mode"] == "read"
    assert client.put(raw, params={"path": bundled}, content="name: Changed").status_code == 403
    assert client.delete(raw, params={"path": bundled}).status_code == 403
    private = write_card(paths.root, "Private")
    paths.cards_dir.mkdir(exist_ok=True)
    alias = paths.cards_dir / "alias.card.yaml"
    alias.symlink_to(private)
    for path in (private, alias):
        assert client.get(raw, params={"path": str(path)}).status_code == 404
        assert client.put(raw, params={"path": str(path)}, content="Changed").status_code == 404
    assert "name: Private" in private.read_text()


def test_invalid_utf8_is_a_load_problem_and_does_not_break_settings(profile_app, paths):
    client, _pid = profile_app
    paths.cards_dir.mkdir(exist_ok=True)
    bad = paths.cards_dir / "binary.card.yaml"
    bad.write_bytes(b"\xff\xfe")
    response = client.get("/api/cards")
    assert response.status_code == 200
    assert response.json()["problems"][0]["path"] == str(bad)
    assert "unreadable" in response.json()["problems"][0]["error"]


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        ("required: [title]", "required: 42", "required"),
        ("required: [title]", "required: title", "required"),
        ("required: [title]", "required: [42]", "required"),
        ("Paperbacks", "2026-10-06", "JSON-compatible"),
        ("Paperbacks", ".nan", "JSON-compatible"),
        ("title: {type: string}", "title: {default: 2026-10-06}", "JSON-compatible"),
        ("text: {path: /title}", "text: 2026-10-06", "JSON-compatible"),
        ("example: {title: Paperbacks}", "example: {title: Paperbacks, 42: Other}", "example"),
    ],
)
def test_invalid_card_shapes_are_diagnosed_without_hiding_valid_cards(
    profile_app, paths, before, after, reason
):
    client, pid = profile_app
    good = write_card(paths.cards_dir, "Shelf")
    bad = paths.cards_dir / "invalid.card.yaml"
    bad.write_text(good.read_text().replace(before, after))
    for url in ("/api/cards", api(pid, "/cards")):
        response = client.get(url)
        assert response.status_code == 200
        assert "Shelf" in {c["name"] for c in response.json()["cards"]}
        problem = next(p for p in response.json()["problems"] if p["path"] == str(bad))
        assert reason in problem["error"]
        assert str(bad) in {f["path"] for f in response.json()["files"]}


@pytest.mark.parametrize("name", ["Book/Shelf", "Shelf/state", "Odd ?#% + & shelf"])
def test_every_accepted_card_name_can_be_managed(profile_app, paths, name):
    client, pid = profile_app
    shared = write_card(paths.cards_dir, "Shelf")
    shared.write_text(shared.read_text().replace("name: Shelf\n", f"name: {json.dumps(name)}\n"))
    identity = {"name": name}
    off = client.post("/api/cards/state", params=identity, json={"enabled": False})
    assert off.status_code == 200
    assert not next(c for c in off.json()["cards"] if c["name"] == name)["enabled"]
    assert (
        client.post("/api/cards/state", params=identity, json={"enabled": True}).status_code == 200
    )
    suppressed = client.post(api(pid, "/cards/suppress"), params=identity)
    assert suppressed.status_code == 200
    assert next(c for c in suppressed.json()["cards"] if c["name"] == name)["suppressed"]
    assert client.delete(api(pid, "/cards/suppress"), params=identity).status_code == 200
    own = write_card(paths.profile_dir(pid) / "workspace" / "cards", "Shelf")
    own.write_text(shared.read_text())
    disabled = client.post(api(pid, "/cards/state"), params=identity, json={"enabled": False})
    assert disabled.status_code == 200
    assert not next(c for c in disabled.json()["cards"] if c["name"] == name)["available"]
    assert client.delete(api(pid, "/cards"), params=identity).status_code == 200
    assert not own.exists() and shared.exists()
    assert client.delete("/api/cards", params=identity).status_code == 200
    assert not shared.exists()
