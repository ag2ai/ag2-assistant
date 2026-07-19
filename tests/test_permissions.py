"""Tests for the folder-permission system and the permission-gated file reader."""

from assistant.folders import READ, READ_WRITE, FolderStore
from assistant.permissions import (
    ALLOW_ONCE,
    DENY,
    GRANT_CHAT,
    GRANT_PROFILE,
    PermissionManager,
    PermissionStore,
    always_allow_command_label,
    command_rule,
    parse_command_rule,
    shell_prefix,
)


class FakeAsker:
    def __init__(self, answer: str):
        self.answer = answer
        self.asked = 0

    async def ask(self, question, timeout=None):
        self.asked += 1
        self.last = question
        return self.answer


def test_store_ignores_legacy_folder_keys(tmp_path):
    """An old-schema permissions.json (with folders/blocked) still loads its
    commands; folder keys are ignored (fresh-install design, ADR 0006)."""
    p = tmp_path / "permissions.json"
    p.write_text('{"folders": ["/tmp/x"], "blocked": ["/tmp/y"], "commands": ["gmail_send"]}')
    store = PermissionStore(path=p)
    assert store.granted_commands() == ["gmail_send"]
    assert not hasattr(store, "granted_folders")


async def test_command_approval_allow_and_deny(tmp_path):
    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), FakeAsker(ALLOW_ONCE))
    assert await mgr.check_command("run_shell_command", "ls") is True

    mgr2 = PermissionManager(PermissionStore(path=tmp_path / "p2.json"), FakeAsker(DENY))
    assert await mgr2.check_command("run_shell_command", "ls") is False


async def test_command_always_allow_persists(tmp_path):
    """An 'always allow the gmail_send tool' answer persists as a bare-tool rule — a
    fresh store over the same path (a new turn) allows without prompting. Action
    tools are the only whole-tool-persistable class."""
    label = always_allow_command_label("gmail_send", None)
    asker = FakeAsker(label)
    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), asker)
    args = '{"to": "a@b.com", "subject": "x", "body": "y"}'
    assert await mgr.check_command("gmail_send", args) is True
    assert await mgr.check_command("gmail_send", args) is True
    assert asker.asked == 1  # turn-sticky (rule cache) after the first grant

    # persisted: a fresh store sees the rule, so a new turn/manager doesn't re-ask
    fresh = PermissionStore(path=tmp_path / "p.json")
    assert fresh.granted_commands() == ["gmail_send"]
    assert fresh.is_command_allowed("gmail_send", None) is True


async def test_code_tools_offer_no_persistent_grant(tmp_path):
    """Host code execution (run_code / run_code_local) has arbitrary-shell authority,
    so the prompt never offers a persistent grant: Allow once / Deny only, and
    nothing is written to the store."""
    from assistant.hitl.base import Question

    seen = {}

    class _Cap:
        async def ask(self, q: Question, timeout=None):
            seen["options"] = q.options
            return ALLOW_ONCE

    for tool in ("run_code", "run_code_local"):
        store = PermissionStore(path=tmp_path / f"{tool}.json")
        mgr = PermissionManager(store, _Cap())
        assert await mgr.check_command(tool, '{"code": "print(1)"}') is True
        assert seen["options"] == [ALLOW_ONCE, DENY]
        assert store.granted_commands() == []


async def test_command_prompt_offers_dynamic_label_not_folder(tmp_path):
    """The action-tool prompt offers the dynamic tool label, not folder wording."""
    from assistant.hitl.base import Question

    seen = {}

    class _CapturingAsker:
        async def ask(self, q: Question, timeout=None):
            seen["options"] = q.options
            return ALLOW_ONCE

    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), _CapturingAsker())
    await mgr.check_command("gmail_send", '{"to": "a@b.com"}')
    assert always_allow_command_label("gmail_send", None) in seen["options"]
    assert GRANT_PROFILE not in seen["options"]  # no folder wording for commands


async def test_command_prompt_states_where_it_runs(tmp_path):
    """The prompt tells the user whether a command runs on the host or sandboxed."""
    from assistant.hitl.base import Question

    seen = {}

    class _Cap:
        async def ask(self, q: Question, timeout=None):
            seen["text"] = q.text
            return DENY

    local = PermissionManager(PermissionStore(path=tmp_path / "a.json"), _Cap(), sandbox="local")
    await local.check_command("run_code", "pip install yfinance")
    assert "computer" in seen["text"].lower()  # clearly the host machine

    docker = PermissionManager(PermissionStore(path=tmp_path / "b.json"), _Cap(), sandbox="docker")
    await docker.check_command("run_code", "pip install yfinance")
    assert "sandbox" in seen["text"].lower()  # clearly isolated


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


# --- read_file_impl (permission-gated reads) ---


def _rf_manager(tmp_path, answer=None):
    store = PermissionStore(path=tmp_path / "p.json")
    folders = FolderStore(path=tmp_path / "folders.json")
    return PermissionManager(
        store, FakeAsker(answer) if answer else None, folders=folders, profile="p1"
    )


async def test_read_file_text_with_permission(tmp_path):
    from assistant.tools.files import read_file_impl

    f = tmp_path / "note.txt"
    f.write_text("hello world")
    result = await read_file_impl(str(f), _rf_manager(tmp_path, ALLOW_ONCE))
    assert "hello world" in result


async def test_read_file_denied(tmp_path):
    from assistant.tools.files import read_file_impl

    f = tmp_path / "note.txt"
    f.write_text("secret")
    result = await read_file_impl(str(f), _rf_manager(tmp_path, DENY))
    assert "denied permission" in result
    assert "secret" not in result


async def test_read_file_missing(tmp_path):
    from assistant.tools.files import read_file_impl

    result = await read_file_impl(str(tmp_path / "nope.txt"), _rf_manager(tmp_path))
    assert "not found" in result.lower()


async def test_read_file_pdf_returns_document(tmp_path):
    from ag2 import ToolResult

    from assistant.tools.files import read_file_impl

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    mgr = _rf_manager(tmp_path)
    mgr.folders.grant_path(tmp_path, READ, "p1")
    result = await read_file_impl(str(pdf), mgr)
    # PDFs return a ToolResult carrying the document for vision reading.
    assert isinstance(result, ToolResult)


def test_command_detail_extracts_code():
    from assistant.permissions import _command_detail

    # run_code-style args: show the code, not the wrapped JSON
    assert _command_detail('{"code": "import os\\nprint(1)"}') == "import os\nprint(1)"
    # shell-style
    assert _command_detail('{"command": "ls -la /tmp"}') == "ls -la /tmp"
    # a dict (already parsed) works too
    assert _command_detail({"script": "build.sh"}) == "build.sh"
    # no known field → key: value lines
    assert "foo: bar" in _command_detail('{"foo": "bar"}')
    # non-JSON → returned as-is
    assert _command_detail("echo hi") == "echo hi"


# --- command rules: parse/build, shell_prefix, store matching, mtime freshness ---


def test_rule_parse_build_inverses():
    # bare tool
    assert parse_command_rule("run_code") == ("run_code", None)
    assert command_rule("run_code") == "run_code"
    assert command_rule("run_code", None) == "run_code"
    # shell prefix rule
    assert parse_command_rule("run_shell_command(git *)") == ("run_shell_command", "git")
    assert command_rule("run_shell_command", "git") == "run_shell_command(git *)"
    # round-trip both directions
    for rule in ("run_code", "run_shell_command(git *)", "gmail_send"):
        assert command_rule(*parse_command_rule(rule)) == rule


def test_rule_parse_rejects_garbage():
    import pytest

    for bad in ("", "   ", "has spaces", "run_shell_command()", "run_shell_command( *)", "a(b"):
        with pytest.raises(ValueError):
            parse_command_rule(bad)


def test_shell_prefix_table():
    # simple → first token
    assert shell_prefix("git status") == "git"
    assert shell_prefix("ls") == "ls"
    assert shell_prefix("/usr/bin/git status") == "/usr/bin/git"  # opaque, not normalised
    # compound / pipe / subshell / redirect → None (never matches, never mints)
    assert shell_prefix("git status && rm x") is None
    assert shell_prefix("a || b") is None
    assert shell_prefix("a ; b") is None
    assert shell_prefix("cat a | grep b") is None
    assert shell_prefix("echo `whoami`") is None
    assert shell_prefix("echo $(whoami)") is None
    assert shell_prefix("echo >x") is None
    assert shell_prefix("cat <x") is None
    assert shell_prefix("a\nb") is None
    # env-var assignment, quotes, empty, too-long → None
    assert shell_prefix("FOO=1 git status") is None
    assert shell_prefix("'quoted") is None
    assert shell_prefix("") is None
    assert shell_prefix(None) is None
    assert shell_prefix("x" * 33 + " y") is None


def test_store_command_round_trip(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    assert store.granted_commands() == []
    store.grant_command("gmail_send")
    store.grant_command("run_shell_command(git *)")
    assert store.granted_commands() == ["gmail_send", "run_shell_command(git *)"]
    # persists to a fresh instance
    fresh = PermissionStore(path=tmp_path / "p.json")
    assert fresh.granted_commands() == ["gmail_send", "run_shell_command(git *)"]


def test_store_bare_tool_matches_commandless_calls_only(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant_command("gmail_send")
    # a bare-tool rule matches invocations that carry no shell command
    assert store.is_command_allowed("gmail_send", None) is True
    # ... but never a shell-like invocation — a hand-edited bare rule on a shell
    # tool must not become "allow everything shell" (see is_command_allowed)
    assert store.is_command_allowed("gmail_send", "anything at all") is False
    # but not a different tool
    assert store.is_command_allowed("run_code", None) is False


def test_store_bare_shell_rule_is_dead_even_if_hand_edited(tmp_path):
    import json

    # Simulate a hand-edited permissions.json containing a blanket shell grant —
    # the matcher must never honour it for actual shell commands.
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"commands": ["run_shell_command"]}))
    store = PermissionStore(path=path)
    assert store.is_command_allowed("run_shell_command", "git status") is False
    assert store.is_command_allowed("run_shell_command", "rm -rf /") is False


def test_store_grant_command_rejects_bare_shell_rules(tmp_path):
    import pytest

    store = PermissionStore(path=tmp_path / "p.json")
    with pytest.raises(ValueError):
        store.grant_command("run_shell_command")
    with pytest.raises(ValueError):
        store.grant_command("run_shell_local")
    assert store.granted_commands() == []
    # prefix rules on shell tools remain grantable
    store.grant_command("run_shell_command(git *)")
    assert store.granted_commands() == ["run_shell_command(git *)"]


def test_store_grant_command_rejects_bare_code_rules(tmp_path):
    """Host code tools execute arbitrary code — same authority class as blanket
    shell, so a persisted whole-tool grant is refused at mint time."""
    import pytest

    store = PermissionStore(path=tmp_path / "p.json")
    for tool in ("run_code", "run_code_local"):
        with pytest.raises(ValueError):
            store.grant_command(tool)
    assert store.granted_commands() == []


def test_store_bare_code_rule_is_dead_even_if_hand_edited(tmp_path):
    import json

    # A hand-edited blanket code grant must never pre-approve code runs.
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"commands": ["run_code", "run_code_local"]}))
    store = PermissionStore(path=path)
    assert store.is_command_allowed("run_code", None) is False
    assert store.is_command_allowed("run_code_local", None) is False


def test_store_concurrent_writers_lose_nothing(tmp_path):
    """Two store instances hammering the same file from two threads must not drop
    mutations. The mutation lock (flock, refresh-under-lock) makes each read-modify-
    write atomic; flock serialises separate fds even within one process, so two
    threads with independent instances model gateway-vs-CLI faithfully."""
    import threading

    path = tmp_path / "p.json"
    a, b = PermissionStore(path=path), PermissionStore(path=path)

    def hammer(store, tag):
        for i in range(25):
            store.grant_command(f"gmail_send({tag}-{i} *)")

    ta = threading.Thread(target=hammer, args=(a, "a"))
    tb = threading.Thread(target=hammer, args=(b, "b"))
    ta.start(), tb.start()
    ta.join(), tb.join()

    final = PermissionStore(path=path).granted_commands()
    assert len(final) == 50  # every grant from both writers survived


def test_store_prefix_matching(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant_command("run_shell_command(git *)")
    # exact prefix matches
    assert store.is_command_allowed("run_shell_command", "git status") is True
    assert store.is_command_allowed("run_shell_command", "git log --oneline") is True
    # compound / wrong-binary / env-prefixed do NOT match
    assert store.is_command_allowed("run_shell_command", "git status && rm x") is False
    assert store.is_command_allowed("run_shell_command", "gitx status") is False
    assert store.is_command_allowed("run_shell_command", "FOO=1 git status") is False
    # a prefix rule does not authorise a different tool
    assert store.is_command_allowed("run_shell_local", "git status") is False


def test_store_revoke_command_hit_and_miss(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant_command("gmail_send")
    assert store.revoke_command("gmail_send") is True
    assert store.revoke_command("gmail_send") is False  # already gone
    assert store.revoke_command("never_granted") is False


def test_store_command_mtime_freshness(tmp_path):
    """A pre-existing instance sees a grant written by another instance (two-process
    CLI ↔ gateway correctness), via the mtime self-refresh."""
    path = tmp_path / "p.json"
    a = PermissionStore(path=path)
    b = PermissionStore(path=path)  # opened before any grant exists
    a.grant_command("run_shell_command(git *)")
    # b re-reads on its next query because the file's (mtime, size) changed
    assert b.is_command_allowed("run_shell_command", "git status") is True
    assert b.granted_commands() == ["run_shell_command(git *)"]


async def test_manager_shell_prompt_persists_prefix_rule(tmp_path):
    """A shell command with a clean first token offers a prefix grant that persists
    as `run_shell_command(git *)` and gates by prefix afterwards."""
    from assistant.hitl.base import Question

    seen = {}

    class _Cap:
        async def ask(self, q: Question, timeout=None):
            seen["options"] = q.options
            return always_allow_command_label("run_shell_command", "git")

    store = PermissionStore(path=tmp_path / "p.json")
    mgr = PermissionManager(store, _Cap())
    assert await mgr.check_command("run_shell_command", '{"command": "git status"}') is True
    assert "Always allow 'git' commands" in seen["options"]
    assert PermissionStore(path=tmp_path / "p.json").granted_commands() == [
        "run_shell_command(git *)"
    ]


async def test_manager_compound_command_offers_allow_once_only(tmp_path):
    """A compound shell command can't be reduced to a prefix → allow-once/deny only,
    nothing persistable is offered."""
    from assistant.hitl.base import Question

    seen = {}

    class _Cap:
        async def ask(self, q: Question, timeout=None):
            seen["options"] = q.options
            return ALLOW_ONCE

    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), _Cap())
    assert await mgr.check_command("run_shell_command", '{"command": "git status && rm x"}') is True
    assert seen["options"] == [ALLOW_ONCE, DENY]  # no "always allow" option


async def test_manager_persisted_grant_skips_prompt(tmp_path):
    """A persisted rule means the manager never prompts — asker untouched."""
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant_command("run_shell_command(git *)")
    asker = FakeAsker(DENY)
    mgr = PermissionManager(store, asker)
    assert await mgr.check_command("run_shell_command", '{"command": "git log"}') is True
    assert asker.asked == 0  # already granted → no prompt


async def test_manager_shell_tool_without_command_offers_no_persist(tmp_path):
    """A shell tool whose args carry no command (degenerate call) must not offer a
    whole-tool 'always allow' — a bare shell grant is never mintable, so offering
    one would crash on grant. Allow once / Deny only."""
    from assistant.hitl.base import Question

    seen = {}

    class _Cap:
        async def ask(self, q: Question, timeout=None):
            seen["options"] = q.options
            return ALLOW_ONCE

    store = PermissionStore(path=tmp_path / "p.json")
    mgr = PermissionManager(store, _Cap())
    assert await mgr.check_command("run_shell_command", "{}") is True
    assert seen["options"] == [ALLOW_ONCE, DENY]
    assert store.granted_commands() == []


async def test_manager_gmail_send_takes_whole_tool_path(tmp_path):
    """An action tool (no `command`/`code` key → whole-tool) offers the tool-named
    label and persists a bare-tool rule."""
    from assistant.hitl.base import Question

    seen = {}

    class _Cap:
        async def ask(self, q: Question, timeout=None):
            seen["options"] = q.options
            return always_allow_command_label("gmail_send", None)

    store = PermissionStore(path=tmp_path / "p.json")
    mgr = PermissionManager(store, _Cap())
    args = '{"to": "a@b.com", "subject": "hi", "body": "hello"}'
    assert await mgr.check_command("gmail_send", args) is True
    assert "Always allow the gmail_send tool" in seen["options"]
    assert PermissionStore(path=tmp_path / "p.json").granted_commands() == ["gmail_send"]


# --- PermissionManager.check: mode-aware, Folder-Grant-minting (ADR 0006) ---


def _manager(tmp_path, asker=None, chat_id="", write_dir=None):
    return PermissionManager(
        PermissionStore(path=tmp_path / "perm.json"),
        asker=asker,
        folders=FolderStore(path=tmp_path / "folders.json"),
        profile="p1",
        chat_id=chat_id,
        workspace_dir=write_dir,
    )


async def test_granted_folder_read_no_prompt(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    (d / "f.txt").write_text("x")
    FolderStore(path=tmp_path / "folders.json").grant_path(d, READ, "p1")
    asker = FakeAsker(DENY)
    assert await _manager(tmp_path, asker).check(d / "f.txt") is True
    assert asker.asked == 0


async def test_read_grant_does_not_cover_write(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    FolderStore(path=tmp_path / "folders.json").grant_path(d, READ, "p1")
    asker = FakeAsker(DENY)
    assert await _manager(tmp_path, asker).check(d / "f.txt", write=True) is False
    assert asker.asked == 1  # prompted (read grant insufficient), user denied


async def test_read_write_grant_covers_both(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    FolderStore(path=tmp_path / "folders.json").grant_path(d, READ_WRITE, "p1")
    m = _manager(tmp_path, FakeAsker(DENY))
    assert await m.check(d / "f.txt") is True
    assert await m.check(d / "f.txt", write=True) is True


async def test_grant_profile_mints_folder_and_grant(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    (d / "f.txt").write_text("x")
    asker = FakeAsker(GRANT_PROFILE)
    assert await _manager(tmp_path, asker).check(d / "f.txt") is True
    fresh = FolderStore(path=tmp_path / "folders.json")
    assert fresh.mode_for(d, "p1") == READ
    assert fresh.list_folders()[0]["name"] == "acme"  # auto-named, renameable


async def test_grant_profile_on_write_mints_read_write(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    asker = FakeAsker(GRANT_PROFILE)
    assert await _manager(tmp_path, asker).check(d / "new.txt", write=True) is True
    assert FolderStore(path=tmp_path / "folders.json").mode_for(d, "p1") == READ_WRITE


async def test_grant_chat_scopes_to_chat(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    asker = FakeAsker(GRANT_CHAT)
    assert await _manager(tmp_path, asker, chat_id="c1").check(d / "f.txt") is True
    fresh = FolderStore(path=tmp_path / "folders.json")
    assert fresh.mode_for(d, "p1", chat_id="c1") == READ
    assert fresh.mode_for(d, "p1", chat_id="c2") is None
    assert fresh.mode_for(d, "p1") is None


async def test_chat_option_absent_without_chat(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    asker = FakeAsker(ALLOW_ONCE)
    await _manager(tmp_path, asker).check(d / "f.txt")  # no chat_id
    assert GRANT_CHAT not in asker.last.options
    asker2 = FakeAsker(ALLOW_ONCE)
    await _manager(tmp_path, asker2, chat_id="c1").check(d / "f.txt")
    assert asker2.last.options == [ALLOW_ONCE, GRANT_CHAT, GRANT_PROFILE, DENY]


async def test_allow_once_persists_nothing_but_caches_turn(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    asker = FakeAsker(ALLOW_ONCE)
    m = _manager(tmp_path, asker)
    assert await m.check(d / "f.txt") is True
    assert await m.check(d / "g.txt") is True  # same folder, same turn: no re-prompt
    assert asker.asked == 1
    assert FolderStore(path=tmp_path / "folders.json").list_folders() == []


async def test_allow_once_read_does_not_cover_write(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    asker = FakeAsker(ALLOW_ONCE)
    m = _manager(tmp_path, asker)
    assert await m.check(d / "f.txt") is True  # read once
    assert await m.check(d / "f.txt", write=True) is True  # re-prompts, allowed once again
    assert asker.asked == 2


async def test_allow_once_write_covers_later_read(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    asker = FakeAsker(ALLOW_ONCE)
    m = _manager(tmp_path, asker)
    assert await m.check(d / "f.txt", write=True) is True  # write once
    assert await m.check(d / "g.txt") is True  # read in same folder: covered
    assert asker.asked == 1


async def test_deny_sets_turn_stance(tmp_path):
    d1, d2 = tmp_path / "a", tmp_path / "b"
    d1.mkdir()
    d2.mkdir()
    asker = FakeAsker(DENY)
    m = _manager(tmp_path, asker)
    assert await m.check(d1 / "f.txt") is False
    assert await m.check(d2 / "f.txt") is False  # no second prompt after a deny
    assert asker.asked == 1


async def test_workspace_is_implicit_read_write(tmp_path):
    ws = tmp_path / "profiles" / "p1" / "workspace"
    ws.mkdir(parents=True)
    asker = FakeAsker(DENY)
    m = _manager(tmp_path, asker, write_dir=ws)
    assert await m.check(ws / "notes.md") is True
    assert await m.check(ws / "sub" / "notes.md", write=True) is True
    assert asker.asked == 0


# --- task-scoped command rules (Task 4: task-run always-allow persists per task) ---


def test_task_scoped_rules_isolated(tmp_path):
    """Task-scoped grants live alongside (not instead of) the global set: a task's
    rule gates only its own turns, a global rule still applies inside a task turn,
    and drop_task wipes exactly that task's rules."""
    s = PermissionStore(path=tmp_path / "perm.json")
    s.grant_command("run_shell_command(git *)", task_id="task-1")
    assert s.is_command_allowed("run_shell_command", "git status", task_id="task-1")
    assert not s.is_command_allowed("run_shell_command", "git status")  # not global
    assert not s.is_command_allowed("run_shell_command", "git status", task_id="task-2")
    assert s.granted_commands(task_id="task-1") == ["run_shell_command(git *)"]
    assert s.granted_commands() == []  # global list untouched

    # global rule still applies inside a task turn
    s.grant_command("run_shell_command(ls *)")
    assert s.is_command_allowed("run_shell_command", "ls -la", task_id="task-1")

    # revoke + drop
    assert s.revoke_command("run_shell_command(git *)", task_id="task-1")
    s.grant_command("run_shell_command(git *)", task_id="task-1")
    s.drop_task("task-1")
    assert s.granted_commands(task_id="task-1") == []


def test_task_scope_keeps_blanket_refusal(tmp_path):
    """The _NO_BLANKET refusal (never mint a bare shell/code-tool rule) applies to
    task-scoped grants too — a task turn must not be able to mint "allow anything"."""
    import pytest

    s = PermissionStore(path=tmp_path / "perm.json")
    with pytest.raises(ValueError):
        s.grant_command("run_shell_command", task_id="task-1")  # bare shell rule stays unmintable


async def test_manager_mints_task_scoped_on_always(tmp_path):
    """A manager bound to a task_id mints its "always allow" grant into that task's
    scope, not the global one — so it persists across that task's future runs but
    never leaks into other chats/tasks."""
    from assistant.hitl.base import Question

    store = PermissionStore(path=tmp_path / "perm.json")

    class _Asker:
        async def ask(self, q: Question, timeout=None):
            return next(o for o in q.options if o.startswith("Always"))  # always_allow_command_label

    pm = PermissionManager(store, _Asker(), task_id="task-9")
    assert await pm.check_command("run_shell_command", '{"command": "git push"}') is True
    assert store.is_command_allowed("run_shell_command", "git push", task_id="task-9")
    assert not store.is_command_allowed("run_shell_command", "git push")  # NOT global
