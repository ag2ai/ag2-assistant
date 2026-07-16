"""Command permissions (Claude-Code-style).

The agent must get permission the first time it runs a shell/code command.
Folder access is governed separately by ``assistant.folders`` (Folders +
Grants, ADR 0006) — this module's store holds ONLY command grants:

  - Commands: shell tools persist a *command-prefix* rule (`tool(prefix *)`);
    code/action tools persist a whole-tool rule (bare tool name). The prompt's
    dynamic "always allow" option reads back what will be persisted.

The store is a plain JSON document (schema ``{"commands": [...]}``) —
hand-editable, rendered directly as Settings rows. It self-refreshes on mtime
change so a long-lived instance (the gateway) sees grants written by another
process (the CLI) or the HTTP API, and every mutation is a read-modify-write
over fresh state.
"""

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

from assistant.hitl.base import Asker, Question

# Cross-platform exclusive file lock for the mutation critical section (_mutate).
# POSIX has flock; Windows locks a byte range via msvcrt (LK_LOCK retries for ~10s
# then raises — loop so contention waits instead of failing a mutation).
if os.name == "nt":  # pragma: no cover — exercised only on Windows
    import msvcrt

    def _lock_exclusive(fh) -> None:
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_exclusive(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_EX)

    def _unlock(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)


ALLOW_ONCE = "Allow once"
GRANT_CHAT = "Allow for this chat"
GRANT_PROFILE = "Always allow in this profile"
DENY = "Deny"

# A persisted command rule is either a bare tool name (`run_code` — a whole-tool
# grant) or `tool(prefix *)` (a shell command-prefix grant). The prefix group must
# start with a non-paren, non-space char so an empty `()` can't parse.
_RULE_RE = re.compile(r"^(?P<tool>[\w.-]+)\((?P<prefix>[^()\s][^()]*) \*\)$")
_TOOL_RE = re.compile(r"^[\w.-]+$")

# Shell metacharacters that turn a command compound: a prefix approval must never
# authorise these (they can smuggle a second command past the approved first token).
_SHELL_META = ("&&", "||", ";", "|", "`", "$(", ">", "<", "\n")
# A prefix is an opaque token — matched verbatim, never path-normalised. This charset
# rejects env-var assignments (`FOO=1 git`), quotes, and globs at prefix-minting time.
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_./-]+$")

# Tools whose invocations carry an arbitrary shell command — these may ONLY be
# granted per-prefix, never as a bare whole-tool rule (that would authorise every
# command, compounds included, defeating the prefix design). Mirrors the names
# registered in tools/__init__.py (SandboxShellTool default + the Docker rename).
SHELL_TOOLS = frozenset({"run_shell_command", "run_shell_local"})
# Host code-execution tools — arbitrary Python has the SAME host authority as
# arbitrary shell (subprocesses, file writes, network), so a persisted blanket
# grant is refused for these too: allow-once per run only. Snippets have no
# prefix-like unit, so there is no narrower persistable rule to offer. The
# *sandboxed* runners carry no approval middleware at all — sandboxed code stays
# friction-free; this guard is strictly about host authority.
CODE_TOOLS = frozenset({"run_code", "run_code_local"})
# The union: no persisted whole-tool grant may exist for these, at mint time
# (grant_command raises) or match time (a hand-edited bare rule never matches).
_NO_BLANKET = SHELL_TOOLS | CODE_TOOLS


def _norm(folder) -> Path:
    return Path(folder).expanduser().resolve()


def parse_command_rule(rule: str) -> tuple[str, str | None]:
    """Split a stored rule string into ``(tool, prefix | None)``.

    Two shapes: a bare tool name (whole-tool grant) or ``tool(prefix *)`` (a shell
    command-prefix grant). Raises ``ValueError`` on anything else, so the CLI and the
    HTTP layer can reject hand-typed garbage before it reaches the store."""
    rule = rule.strip()
    m = _RULE_RE.match(rule)
    if m:
        return m.group("tool"), m.group("prefix")
    if _TOOL_RE.match(rule):
        return rule, None
    raise ValueError(f"not a valid permission rule: {rule!r}")


def command_rule(tool: str, prefix: str | None = None) -> str:
    """Build the canonical stored rule string — the inverse of parse_command_rule."""
    return f"{tool}({prefix} *)" if prefix else tool


def shell_prefix(command: str | None) -> str | None:
    """The first token of a SIMPLE shell command — the unit a prefix rule matches on.

    Returns ``None`` (→ never matches a rule, never mints one, always re-prompts) when
    the command is compound (contains ``&&``, ``||``, ``;``, ``|``, a backtick, ``$(``,
    ``>``, ``<``, or a newline): those can smuggle a second command past a prefix
    approval, so we refuse to reduce them to a single prefix. Also ``None`` when the
    first token is empty, too long, or carries anything outside the prefix charset
    (kills ``FOO=1 git …``, quotes, globs). Prefixes are OPAQUE tokens matched verbatim
    — no path normalisation, so ``git`` ≠ ``/usr/bin/git`` by design (the user approved
    the exact string they saw)."""
    if not command:
        return None
    if any(meta in command for meta in _SHELL_META):
        return None
    parts = command.strip().split()
    if not parts:
        return None
    token = parts[0]
    if len(token) > 32 or not _PREFIX_RE.match(token):
        return None
    return token


def _shell_command(arguments) -> str | None:
    """Pull a shell command string out of tool arguments, or ``None`` for a non-shell
    (code/action) tool. Parsed the same way as ``_command_detail``; a truthy
    ``command`` field marks a shell tool (→ prefix path), else the caller takes the
    whole-tool path. No tool-name registry — the args shape alone decides."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if isinstance(data, dict) and data.get("command"):
        return str(data["command"])
    return None


def always_allow_command_label(tool: str, prefix: str | None) -> str:
    """Single source of truth for the dynamic 'always allow' option wording. The
    manager builds the prompt with it AND matches the returned answer against it, so
    the two can never drift. A shell prefix → command-family wording; everything else
    names the tool explicitly (so an action tool like ``gmail_send`` reads honestly)."""
    if prefix:
        return f"Always allow '{prefix}' commands"
    return f"Always allow the {tool} tool"


def _command_detail(arguments) -> str:
    """Human-readable preview of what a shell/code tool will run — so the user can
    see the actual code/command before approving. Pulls the meaningful field out of
    the tool arguments (code/command/script/cmd) rather than showing wrapped JSON."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    try:
        data = json.loads(raw)
    except Exception:
        return str(arguments).strip()
    if isinstance(data, dict):
        for key in ("code", "command", "script", "cmd", "snippet"):
            if data.get(key):
                return str(data[key]).strip()
        return "\n".join(f"{k}: {v}" for k, v in data.items()).strip()
    return str(arguments).strip()


class PermissionStore:
    """Persistent record of the commands the user has granted access to."""

    def __init__(self, path: Path | None) -> None:
        # `path` is REQUIRED (no global default). Pass ``None`` only for an explicit
        # ephemeral, non-persisting store (e.g. an un-wired fallback) — there is no
        # implicit on-disk location.
        self._path = Path(path) if path is not None else None
        self._commands: set[str] = set()
        # (st_mtime_ns, st_size) of the file when we last read it — the freshness key
        # for _refresh(). None means "no file loaded" (missing/ephemeral).
        self._stat: tuple[int, int] | None = None
        self._load()

    def _load(self) -> None:
        self._commands = set()
        self._stat = None
        if self._path is None:
            return
        try:
            st = self._path.stat()
        except OSError:
            return  # missing → empty (as today)
        # Record what we read even if it's corrupt, so _refresh() doesn't re-read a
        # broken file on every single query until it changes.
        self._stat = (st.st_mtime_ns, st.st_size)
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return  # exists but corrupt → empty
        self._commands = set(data.get("commands", []))

    def _refresh(self) -> None:
        """Re-load from disk when the file changed since our last read. Makes a
        long-lived store (the gateway's) see grants written by another process (the
        CLI) or the HTTP API. Cheap: one stat() call, and we only re-read when
        (mtime, size) — or file existence — actually changed."""
        if self._path is None:
            return
        try:
            st = self._path.stat()
            current: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            current = None  # file disappeared
        if current != self._stat:
            self._load()

    @contextlib.contextmanager
    def _mutate(self):
        """Serialise read-modify-write across processes AND instances.

        Every mutation is refresh → change → save. Without a lock, two writers can
        interleave (both refresh, both save) and the second save silently drops the
        first — worst case a freshly minted command grant clobbered by a stale
        writer, i.e. a lost grant. An exclusive lock on a sidecar lock file (flock on POSIX,
        msvcrt byte-range on Windows — see _lock_exclusive) makes the whole sequence
        atomic; it serialises separate fds even within one process, so this covers
        gateway-vs-CLI, two gateways, and two store instances alike. Queries stay
        lock-free (atomic replace means they never see a torn file)."""
        if self._path is None:
            yield  # ephemeral store — single-instance by construction
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.parent / (self._path.name + ".lock")
        with open(lock_path, "w") as lock:
            _lock_exclusive(lock)
            try:
                self._refresh()  # fresh state UNDER the lock — nobody can interleave
                yield
            finally:
                _unlock(lock)

    def _save(self) -> None:
        if self._path is None:
            return  # ephemeral store — nothing to persist
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"commands": sorted(self._commands)}, indent=2)
        # Atomic write: temp file in the SAME directory + os.replace, so a concurrent
        # reader never sees a half-written file (torn read). os.replace is atomic only
        # within a filesystem — same-dir guarantees that.
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".permissions.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        # Adopt the just-written file's stat as our snapshot so the next _refresh()
        # doesn't needlessly re-read our own write.
        try:
            st = self._path.stat()
            self._stat = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._stat = None

    # ---- commands ----

    def is_command_allowed(self, tool: str, command: str | None) -> bool:
        """True if a stored rule authorises this invocation. A bare-tool rule matches
        calls that carry NO shell command (code/action tools); a prefix rule matches
        only when ``shell_prefix(command)`` equals the rule's prefix — the SAME
        function used at grant time, so grant and match can't disagree.

        A bare rule deliberately never matches an arbitrary-execution invocation —
        shell-like (carries a command string) or a host code tool (_NO_BLANKET):
        "allow everything" must not be mintable for those — not via the API/CLI
        (they reject it) and not by hand-editing the JSON (this guard). Shell trust
        is per-prefix only; host code runs are approved per run."""
        self._refresh()
        prefix = shell_prefix(command) if command else None
        for rule in self._commands:
            try:
                r_tool, r_prefix = parse_command_rule(rule)
            except ValueError:
                continue  # ignore a hand-corrupted rule rather than crash a turn
            if r_tool != tool:
                continue
            if r_prefix is None:
                if command is None and r_tool not in _NO_BLANKET:
                    return True  # whole-tool grant — action tools only
                continue
            if prefix is not None and prefix == r_prefix:
                return True
        return False

    def grant_command(self, rule: str) -> None:
        """Persist a command rule, canonicalised via parse→build (so a stray form —
        extra spaces, etc. — normalises).

        Raises ``ValueError`` for a bare grant on a shell or host-code tool: that
        rule would be dead at match time (see is_command_allowed) and reads as
        "allow arbitrary execution forever" — reject it here so every mint path
        (API, CLI, prompt) fails loudly instead."""
        tool, prefix = parse_command_rule(rule)
        if prefix is None and tool in SHELL_TOOLS:
            raise ValueError(
                f"{tool} needs a prefix rule (e.g. '{tool}(git *)') — "
                "a blanket shell grant would cover any command, compounds included"
            )
        if prefix is None and tool in CODE_TOOLS:
            raise ValueError(
                f"{tool} executes arbitrary code on this computer — it can't be "
                "always-allowed; each run is approved individually"
            )
        with self._mutate():
            self._commands.add(command_rule(tool, prefix))
            self._save()

    def revoke_command(self, rule: str) -> bool:
        try:
            canonical = command_rule(*parse_command_rule(rule))
        except ValueError:
            canonical = rule.strip()  # unparseable → only matches if literally stored
        with self._mutate():
            if canonical in self._commands:
                self._commands.discard(canonical)
                self._save()
                return True
            return False

    def granted_commands(self) -> list[str]:
        self._refresh()
        return sorted(self._commands)


class PermissionManager:
    """The single, turn-level permission authority — all access tools go through it.

    One instance is created per user turn (per `send_message`) and shared by every
    access tool (`read_file`, shell, code). It holds:
      - the persistent command-grant store (`store`, ADR pre-0006 shape, survives
        turns) and the persistent Folder/Grant store (`folders`, ADR 0006),
      - turn-scoped decisions (folders/commands allowed or denied this turn),
      - a turn-level stance: once the user denies *anything*, stop asking for new
        access for the rest of the turn (kills prompt-spam and tool escalation).

    Already-granted access still works after a deny; only *new* prompts are
    suppressed. A new turn starts fresh (a new manager), except persisted grants.
    """

    def __init__(
        self,
        store: PermissionStore | None = None,
        asker: Asker | None = None,
        sandbox: str = "local",
        folders=None,
        profile: str = "",
        chat_id: str = "",
        workspace_dir=None,
    ) -> None:
        from assistant.folders import FolderStore

        self.store = store if store is not None else PermissionStore(path=None)
        self.folders = folders if folders is not None else FolderStore(path=None)
        self.asker = asker
        self.sandbox = sandbox
        self.profile = profile
        self.chat_id = (chat_id or "").strip()
        # The profile's own Files space (CONTEXT.md "Files"): always read+write,
        # no Grant needed — Folders govern only paths outside the Root.
        self.workspace_dir = _norm(workspace_dir) if workspace_dir else None
        self._denied_folders: set[str] = set()
        # Turn-scoped allow-once: folder path -> write allowed too?
        self._once: dict[str, bool] = {}
        self._cmd_allowed: set[str] = set()
        self._cmd_denied: set[str] = set()
        self._any_denied = False

    async def check(self, target, write: bool = False) -> bool:
        """Ensure access to ``target``'s folder at the needed mode, prompting if
        needed (turn-scoped). ``write=True`` requires a read_write Grant; plain
        reads accept either mode (write implies read). Approving the prompt at
        chat/profile scope auto-creates the Folder + Grant (ADR 0006)."""
        from assistant.folders import READ, READ_WRITE

        target = Path(target).expanduser()
        folder = _norm(target if target.is_dir() else target.parent)

        if self.workspace_dir is not None and (
            folder == self.workspace_dir or self.workspace_dir in folder.parents
        ):
            return True
        mode = self.folders.mode_for(folder, self.profile, self.chat_id)
        if mode == READ_WRITE or (mode == READ and not write):
            return True
        key = str(folder)
        if key in self._once and (self._once[key] or not write):
            return True
        if key in self._denied_folders or self._any_denied:
            return False
        if self.asker is None:
            return False

        verb = "write in" if write else "read"
        options = [ALLOW_ONCE]
        if self.chat_id:
            options.append(GRANT_CHAT)
        options += [GRANT_PROFILE, DENY]
        scope_hint = (
            "Allow just this once, grant it to this chat, always allow it in this profile, or deny."
            if self.chat_id
            else "Allow just this once, always allow it in this profile, or deny."
        )
        answer = await self.asker.ask(
            Question(
                text=f"Allow AG2 Assistant to {verb} {folder.name or folder}?",
                detail=(
                    f"AG2 Assistant wants {'write' if write else 'read'} access to "
                    f"{folder} (for {target.name}). {scope_hint}"
                ),
                options=options,
                kind="permission",
            )
        )

        minted = READ_WRITE if write else READ
        if answer == GRANT_PROFILE:
            self.folders.grant_path(folder, minted, self.profile)
            return True
        if answer == GRANT_CHAT and self.chat_id:
            self.folders.grant_path(folder, minted, self.profile, self.chat_id)
            return True
        if answer == ALLOW_ONCE:
            self._once[key] = write or self._once.get(key, False)
            return True
        self._denied_folders.add(key)
        self._any_denied = True
        return False

    async def check_command(self, tool_name: str, arguments) -> bool:
        """Approve a shell/code command, prompting if needed (turn-scoped).

        Same authority as folder access, so a denial anywhere this turn stops
        further command prompts too. Persisted grants (this or any past turn, another
        profile, or the CLI/API) skip the prompt entirely.
        """
        command = _shell_command(arguments)
        prefix = shell_prefix(command) if command else None

        # Persisted grant wins first — an earlier "always allow" means no prompt.
        if self.store.is_command_allowed(tool_name, command):
            return True

        # The rule string is our turn-cache key (see __init__): sticky even when the
        # store is ephemeral and persists nothing.
        rule = command_rule(tool_name, prefix)
        if rule in self._cmd_allowed:
            return True
        if rule in self._cmd_denied or self._any_denied:
            return False
        if self.asker is None:
            return False

        detail = _command_detail(arguments)
        if len(detail) > 4000:
            detail = detail[:4000] + "\n… (truncated)"
        if self.sandbox == "docker":
            where = "in an isolated Docker sandbox (no access to your files)"
        else:
            where = "on your computer — NOT sandboxed (it can touch your files)"

        # Dynamic "always allow" option, built from the SAME label helper we match the
        # answer against. A shell command with a clean first token offers a
        # command-family grant (persists `tool(prefix *)`); an action tool (gmail_send
        # etc.) offers a whole-tool grant (persists the bare tool). Nothing safe to
        # persist → allow-once/deny only: a shell command we can't reduce to a prefix
        # (compound/weird), or an arbitrary-execution tool with no prefix unit — host
        # code tools are approved per run, a blanket grant is never mintable.
        if prefix is not None:
            always_label: str | None = always_allow_command_label(tool_name, prefix)
        elif command is None and tool_name not in _NO_BLANKET:
            always_label = always_allow_command_label(tool_name, None)
        else:
            always_label = None

        options = [ALLOW_ONCE]
        if always_label is not None:
            options.append(always_label)
        options.append(DENY)

        answer = await self.asker.ask(
            Question(
                text=f"Allow AG2 Assistant to run {tool_name} {where}?",
                detail=detail,
                options=options,
                kind="permission",
            )
        )

        if always_label is not None and answer == always_label:
            # Persist (mtime-refresh means live turns see it on their next query) AND
            # cache for this turn (covers the ephemeral-store case).
            self.store.grant_command(rule)
            self._cmd_allowed.add(rule)
            return True
        if answer == ALLOW_ONCE:
            return True
        self._cmd_denied.add(rule)
        self._any_denied = True
        return False
