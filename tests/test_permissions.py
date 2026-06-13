"""Tests for the folder-permission system and the permission-gated file reader."""

from agclaw.permissions import (
    ALLOW_ONCE,
    ALWAYS_ALLOW,
    DENY,
    PermissionManager,
    PermissionStore,
    request_access,
)


class FakeAsker:
    def __init__(self, answer: str):
        self.answer = answer
        self.asked = 0

    async def ask(self, question, timeout=None):
        self.asked += 1
        self.last = question
        return self.answer


def test_grant_and_is_allowed(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    folder = tmp_path / "docs"
    folder.mkdir()
    assert store.is_allowed(folder) is False
    store.grant(folder)
    assert store.is_allowed(folder) is True


def test_ancestor_grant_covers_subfolders(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    (tmp_path / "a" / "b").mkdir(parents=True)
    store.grant(tmp_path / "a")
    assert store.is_allowed(tmp_path / "a" / "b") is True


def test_persistence_across_instances(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant(tmp_path / "docs")
    reloaded = PermissionStore(path=tmp_path / "p.json")
    assert reloaded.is_allowed(tmp_path / "docs") is True


def test_revoke(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant(tmp_path / "docs")
    assert store.revoke(tmp_path / "docs") is True
    assert store.is_allowed(tmp_path / "docs") is False


async def test_request_access_already_allowed_does_not_ask(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant(tmp_path)
    asker = FakeAsker(DENY)
    assert await request_access(tmp_path / "f.txt", store, asker) is True
    assert asker.asked == 0  # no prompt when already granted


async def test_request_access_always_persists(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    asker = FakeAsker(ALWAYS_ALLOW)
    assert await request_access(f, store, asker) is True
    # persisted: a fresh store sees it
    assert PermissionStore(path=tmp_path / "p.json").is_allowed(tmp_path) is True


async def test_request_access_once_does_not_persist(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    asker = FakeAsker(ALLOW_ONCE)
    assert await request_access(f, store, asker) is True
    assert PermissionStore(path=tmp_path / "p.json").is_allowed(tmp_path) is False


async def test_request_access_deny(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert await request_access(f, store, FakeAsker(DENY)) is False


async def test_request_access_no_asker_denies(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert await request_access(f, store, None) is False


# --- read_file_impl ---


def _manager(tmp_path, answer=None):
    store = PermissionStore(path=tmp_path / "p.json")
    return PermissionManager(store, FakeAsker(answer) if answer else None)


async def test_manager_check_grants_and_persists(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    mgr = _manager(tmp_path, ALWAYS_ALLOW)
    assert await mgr.check(f) is True
    assert mgr.is_allowed(tmp_path) is True


async def test_manager_check_deny(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert await _manager(tmp_path, DENY).check(f) is False


async def test_manager_deny_is_sticky_within_turn(tmp_path):
    """After a deny, the same folder is not re-prompted this turn."""
    f = tmp_path / "f.txt"
    f.write_text("x")
    asker = FakeAsker(DENY)
    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), asker)
    assert await mgr.check(f) is False
    assert await mgr.check(f) is False
    assert asker.asked == 1  # asked once, then remembered the no


async def test_command_approval_allow_and_deny(tmp_path):
    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), FakeAsker(ALLOW_ONCE))
    assert await mgr.check_command("run_shell_command", "ls") is True

    mgr2 = PermissionManager(PermissionStore(path=tmp_path / "p2.json"), FakeAsker(DENY))
    assert await mgr2.check_command("run_shell_command", "ls") is False


async def test_command_always_allow_is_turn_sticky(tmp_path):
    asker = FakeAsker(ALWAYS_ALLOW)
    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), asker)
    assert await mgr.check_command("run_code", "print(1)") is True
    assert await mgr.check_command("run_code", "print(2)") is True
    assert asker.asked == 1  # always-allow remembered for the turn


async def test_turn_deny_stops_asking_across_tools(tmp_path):
    """A folder deny suppresses further command prompts this turn (unified turn-level)."""
    f = tmp_path / "f.txt"
    f.write_text("x")
    asker = FakeAsker(DENY)
    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), asker)
    assert await mgr.check(f) is False  # folder deny → sets the turn stance
    # command check should now auto-deny WITHOUT another prompt
    assert await mgr.check_command("run_shell_command", "cat f.txt") is False
    assert asker.asked == 1


async def test_read_file_text_with_permission(tmp_path):
    from agclaw.tools.files import read_file_impl

    f = tmp_path / "note.txt"
    f.write_text("hello world")
    result = await read_file_impl(str(f), _manager(tmp_path, ALWAYS_ALLOW))
    assert "hello world" in result


async def test_read_file_denied(tmp_path):
    from agclaw.tools.files import read_file_impl

    f = tmp_path / "note.txt"
    f.write_text("secret")
    result = await read_file_impl(str(f), _manager(tmp_path, DENY))
    assert "denied permission" in result
    assert "secret" not in result


async def test_read_file_missing(tmp_path):
    from agclaw.tools.files import read_file_impl

    result = await read_file_impl(str(tmp_path / "nope.txt"), _manager(tmp_path))
    assert "not found" in result.lower()


async def test_read_file_pdf_returns_document(tmp_path):
    from autogen.beta import ToolResult

    from agclaw.tools.files import read_file_impl

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    mgr = _manager(tmp_path)
    mgr.grant(tmp_path)
    result = await read_file_impl(str(pdf), mgr)
    # PDFs return a ToolResult carrying the document for vision reading.
    assert isinstance(result, ToolResult)
