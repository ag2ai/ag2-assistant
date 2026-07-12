"""Tests for the folder-permission system and the permission-gated file reader."""

from assistant.permissions import (
    ALLOW_ONCE,
    ALWAYS_ALLOW,
    DENY,
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


def test_block_and_unblock(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.block(tmp_path / "secret")
    assert store.is_blocked(tmp_path / "secret") is True
    assert store.is_blocked(tmp_path / "secret" / "sub") is True  # ancestor covers
    # persists
    assert PermissionStore(path=tmp_path / "p.json").is_blocked(tmp_path / "secret")
    assert store.unblock(tmp_path / "secret") is True
    assert store.is_blocked(tmp_path / "secret") is False


def test_block_removes_existing_grant(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant(tmp_path / "x")
    store.block(tmp_path / "x")
    assert store.is_allowed(tmp_path / "x") is False
    assert store.is_blocked(tmp_path / "x") is True


async def test_manager_blocked_never_asks(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    store = PermissionStore(path=tmp_path / "p.json")
    store.block(tmp_path)
    asker = FakeAsker(ALWAYS_ALLOW)
    mgr = PermissionManager(store, asker)
    assert await mgr.check(f) is False
    assert asker.asked == 0  # blocked → never prompts


async def test_check_already_allowed_does_not_ask(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant(tmp_path)
    asker = FakeAsker(DENY)
    assert await PermissionManager(store, asker).check(tmp_path / "f.txt") is True
    assert asker.asked == 0  # no prompt when already granted


async def test_check_always_persists(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    asker = FakeAsker(ALWAYS_ALLOW)
    assert await PermissionManager(store, asker).check(f) is True
    # persisted: a fresh store sees it
    assert PermissionStore(path=tmp_path / "p.json").is_allowed(tmp_path) is True


async def test_check_once_does_not_persist(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    asker = FakeAsker(ALLOW_ONCE)
    assert await PermissionManager(store, asker).check(f) is True
    assert PermissionStore(path=tmp_path / "p.json").is_allowed(tmp_path) is False


async def test_check_deny(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert await PermissionManager(store, FakeAsker(DENY)).check(f) is False


async def test_check_no_asker_denies(tmp_path):
    store = PermissionStore(path=tmp_path / "p.json")
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert await PermissionManager(store, None).check(f) is False


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
    """The action-tool prompt offers the dynamic tool label, not the folder option."""
    from assistant.hitl.base import Question

    seen = {}

    class _CapturingAsker:
        async def ask(self, q: Question, timeout=None):
            seen["options"] = q.options
            return ALLOW_ONCE

    mgr = PermissionManager(PermissionStore(path=tmp_path / "p.json"), _CapturingAsker())
    await mgr.check_command("gmail_send", '{"to": "a@b.com"}')
    assert always_allow_command_label("gmail_send", None) in seen["options"]
    assert ALWAYS_ALLOW not in seen["options"]  # no folder wording for commands


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


async def test_read_file_text_with_permission(tmp_path):
    from assistant.tools.files import read_file_impl

    f = tmp_path / "note.txt"
    f.write_text("hello world")
    result = await read_file_impl(str(f), _manager(tmp_path, ALWAYS_ALLOW))
    assert "hello world" in result


async def test_read_file_denied(tmp_path):
    from assistant.tools.files import read_file_impl

    f = tmp_path / "note.txt"
    f.write_text("secret")
    result = await read_file_impl(str(f), _manager(tmp_path, DENY))
    assert "denied permission" in result
    assert "secret" not in result


async def test_read_file_missing(tmp_path):
    from assistant.tools.files import read_file_impl

    result = await read_file_impl(str(tmp_path / "nope.txt"), _manager(tmp_path))
    assert "not found" in result.lower()


async def test_read_file_pdf_returns_document(tmp_path):
    from ag2 import ToolResult

    from assistant.tools.files import read_file_impl

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    mgr = _manager(tmp_path)
    mgr.grant(tmp_path)
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
    path.write_text(json.dumps({"folders": [], "blocked": [], "commands": ["run_shell_command"]}))
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
    path.write_text(
        json.dumps({"folders": [], "blocked": [], "commands": ["run_code", "run_code_local"]})
    )
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
            store.grant(f"/tmp/{tag}-{i}")

    ta = threading.Thread(target=hammer, args=(a, "a"))
    tb = threading.Thread(target=hammer, args=(b, "b"))
    ta.start(), tb.start()
    ta.join(), tb.join()

    final = PermissionStore(path=path).granted_folders()
    assert len(final) == 50  # every grant from both writers survived


def test_store_stale_writer_cannot_clobber_fresh_block(tmp_path):
    """The Codex-review scenario: instance A loads OLD state, instance B blocks a
    folder, then A mutates. A's mutation must re-read under the lock and preserve
    B's block — a deny boundary must never be silently dropped by a stale writer."""
    path = tmp_path / "p.json"
    a = PermissionStore(path=path)  # A loads (empty) state and goes stale
    b = PermissionStore(path=path)
    b.block("/tmp/secret")  # B writes a fresh deny boundary
    a.grant("/tmp/elsewhere")  # stale A mutates — must merge, not clobber

    final = PermissionStore(path=path)
    assert final.is_blocked("/tmp/secret") is True  # B's block survived
    assert final.is_allowed("/tmp/elsewhere") is True  # A's grant landed too


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


def test_store_folder_mtime_freshness(tmp_path):
    path = tmp_path / "p.json"
    a = PermissionStore(path=path)
    b = PermissionStore(path=path)
    a.grant(tmp_path / "docs")
    assert b.is_allowed(tmp_path / "docs") is True


def test_block_wins_over_parent_grant_for_whole_subtree(tmp_path):
    """Parent grant + child block is a supported shape: the block denies the child
    AND everything beneath it (subtree-wide), at the store level — is_allowed itself
    returns False inside the blocked subtree, so no caller can honour the parent
    grant there by skipping the is_blocked check. The parent stays usable outside
    the blocked subtree, and unblocking restores the parent grant's coverage."""
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant(tmp_path / "proj")
    store.block(tmp_path / "proj" / "secrets")

    # blocked child + grandchildren: denied even though the parent grant covers them
    assert store.is_allowed(tmp_path / "proj" / "secrets") is False
    assert store.is_allowed(tmp_path / "proj" / "secrets" / "deep") is False
    assert store.is_blocked(tmp_path / "proj" / "secrets" / "deep") is True
    # the parent grant still works outside the blocked subtree
    assert store.is_allowed(tmp_path / "proj") is True
    assert store.is_allowed(tmp_path / "proj" / "src") is True
    # unblock → the parent grant covers the child again (deliberate coexistence)
    assert store.unblock(tmp_path / "proj" / "secrets") is True
    assert store.is_allowed(tmp_path / "proj" / "secrets") is True


def test_parent_block_denies_child_grant(tmp_path):
    """A grant deeper than a block never wins: blocking a parent denies the whole
    subtree even where an explicit child grant exists."""
    store = PermissionStore(path=tmp_path / "p.json")
    store.grant(tmp_path / "vault" / "notes")
    store.block(tmp_path / "vault")
    assert store.is_allowed(tmp_path / "vault" / "notes") is False
    assert store.is_blocked(tmp_path / "vault" / "notes") is True


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
