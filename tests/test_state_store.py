"""StateStore — the document-level guarantees of the shared state store (ADR 0016).

Resolution itself (default-on, Suppression, purge by kind) is driven through its
first consumer in tests/test_skills_state.py; what is pinned here is what belongs to
the store rather than to skills: one store per document, the on-disk shape, and the
cross-process concurrency.
"""

import json
import subprocess
import sys

from assistant.state_store import DISABLE_OWN, SUPPRESS_SHARED, StateStore


def _store(tmp_path, name="things.json"):
    return StateStore(path=tmp_path / name)


# --- one store per document -----------------------------------------------------


def test_two_documents_are_independent(tmp_path):
    """A second store over a different document neither reads nor writes the first."""
    things = _store(tmp_path, "things.json")
    others = _store(tmp_path, "others.json")
    things.set_enabled("weather", False)
    things.set_suppressed("weather", "work", True)
    assert others.is_available("weather", "work") is True
    assert others.disabled_names() == set()
    assert not (tmp_path / "others.json").exists()  # a read alone writes nothing


def test_purge_cannot_reach_another_documents_record(tmp_path):
    things = _store(tmp_path, "things.json")
    others = _store(tmp_path, "others.json")
    things.set_suppressed("weather", "work", True, kind=SUPPRESS_SHARED)
    others.set_suppressed("weather", "work", True, kind=SUPPRESS_SHARED)
    others.purge("weather")
    assert things.is_suppressed("weather", "work") is True
    assert others.is_suppressed("weather", "work") is False


# --- the document on disk -------------------------------------------------------


def test_reading_never_rewrites_the_document(tmp_path):
    p = tmp_path / "things.json"
    p.write_text('{"disabled": ["weather"], "suppressed": [{"profile": "work", "name": "news"}]}')
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    store = StateStore(path=p)
    assert store.is_available("weather") is False
    assert store.is_suppressed("news", "work") is True
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before


def test_written_document_keeps_its_shape(tmp_path):
    """The shape an installed skills.json already has: a `disabled` name list and
    `suppressed` {profile, name, kind} records."""
    store = _store(tmp_path)
    store.set_enabled("weather", False)
    store.set_suppressed("news", "work", True, kind=DISABLE_OWN)
    assert json.loads((tmp_path / "things.json").read_text()) == {
        "disabled": ["weather"],
        "suppressed": [{"profile": "work", "name": "news", "kind": "own"}],
    }


def test_ephemeral_store_persists_nothing(tmp_path):
    store = StateStore(path=None)
    store.set_enabled("weather", False)
    assert store.is_disabled("weather") is True
    assert list(tmp_path.iterdir()) == []


# --- concurrency ----------------------------------------------------------------


def test_cross_instance_refresh_picks_up_writes(tmp_path):
    """A long-lived reader sees another writer's change on its next query (mtime
    self-refresh), the same guarantee FolderStore gives the gateway."""
    reader = _store(tmp_path)
    writer = _store(tmp_path)
    assert reader.is_available("weather") is True
    writer.set_enabled("weather", False)
    assert reader.is_available("weather") is False


_WRITER = """
import sys
from pathlib import Path
from assistant.state_store import StateStore

doc, tag = Path(sys.argv[1]), sys.argv[2]
for i in range(10):
    StateStore(doc).set_enabled(f"{tag}-{i}", False)
"""


def test_concurrent_processes_do_not_lose_each_others_records(tmp_path):
    """Four processes writing the same document at once: the exclusive lock around
    each read-modify-write means every record survives."""
    doc = tmp_path / "things.json"
    writers = [
        subprocess.Popen([sys.executable, "-c", _WRITER, str(doc), tag])
        for tag in ("a", "b", "c", "d")
    ]
    assert [w.wait(timeout=60) for w in writers] == [0, 0, 0, 0]
    expected = {f"{tag}-{i}" for tag in "abcd" for i in range(10)}
    assert StateStore(path=doc).disabled_names() == expected
