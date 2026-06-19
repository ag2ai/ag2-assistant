"""Folder-access permissions (Claude-Code-style).

The agent must get permission the first time it accesses a folder. Grants are
per-folder (covering files and subfolders), persisted to disk, with three options
on first access: Allow once / Always allow this folder / Deny.
"""

import json
from pathlib import Path

from assistant.hitl.base import Asker, Question

ALLOW_ONCE = "Allow once"
ALWAYS_ALLOW = "Always allow this folder"
ALWAYS_ALLOW_CMD = "Always allow this command"  # shell/code aren't folder-scoped
DENY = "Deny"


def _norm(folder) -> Path:
    return Path(folder).expanduser().resolve()


class PermissionStore:
    """Persistent record of folders the user has granted access to."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (Path.home() / ".ag2assistant" / "permissions.json")
        self._granted: set[str] = set()
        self._blocked: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._granted = set(data.get("folders", []))
                self._blocked = set(data.get("blocked", []))
            except Exception:
                self._granted, self._blocked = set(), set()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {"folders": sorted(self._granted), "blocked": sorted(self._blocked)},
                indent=2,
            )
        )

    @staticmethod
    def _covers(folders: set[str], folder: Path) -> bool:
        for g in folders:
            gp = Path(g)
            if folder == gp or gp in folder.parents:
                return True
        return False

    def is_allowed(self, folder) -> bool:
        """True if the folder or any ancestor has been granted."""
        return self._covers(self._granted, _norm(folder))

    def is_blocked(self, folder) -> bool:
        """True if the folder or any ancestor is permanently blocked."""
        return self._covers(self._blocked, _norm(folder))

    def grant(self, folder) -> None:
        self._granted.add(str(_norm(folder)))
        self._save()

    def revoke(self, folder) -> bool:
        key = str(_norm(folder))
        if key in self._granted:
            self._granted.discard(key)
            self._save()
            return True
        return False

    def block(self, folder) -> None:
        """Permanently deny a folder (and remove any conflicting grant)."""
        key = str(_norm(folder))
        self._blocked.add(key)
        self._granted.discard(key)
        self._save()

    def unblock(self, folder) -> bool:
        key = str(_norm(folder))
        if key in self._blocked:
            self._blocked.discard(key)
            self._save()
            return True
        return False

    def granted_folders(self) -> list[str]:
        return sorted(self._granted)

    def blocked_folders(self) -> list[str]:
        return sorted(self._blocked)


class PermissionManager:
    """The single, turn-level permission authority — all access tools go through it.

    One instance is created per user turn (per `send_message`) and shared by every
    access tool (`read_file`, shell, code). It holds:
      - the persistent grant store (folder "always allow", survives turns),
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
    ) -> None:
        self.store = store or PermissionStore()
        self.asker = asker
        self.sandbox = sandbox  # "local" (host) or "docker" (isolated) — shown in prompts
        self._denied_folders: set[str] = set()
        self._cmd_allowed: set[str] = set()
        self._cmd_denied: set[str] = set()
        self._any_denied = False  # user said no to something this turn

    async def check(self, target) -> bool:
        """Ensure access to `target`'s folder, prompting if needed (turn-scoped)."""
        target = Path(target).expanduser()
        folder = _norm(target if target.is_dir() else target.parent)

        if self.store.is_blocked(folder):
            return False  # permanently blocked → never ask
        if self.store.is_allowed(folder):
            return True
        if str(folder) in self._denied_folders or self._any_denied:
            return False  # denied / user already said no this turn → don't ask
        if self.asker is None:
            return False

        answer = await self.asker.ask(
            Question(
                text=f"Allow AG2 Assistant to read {folder.name or folder}?",
                detail=f"AG2 Assistant wants to access {folder} (to read {target.name}). "
                "Allow just this once, always allow this folder, or deny.",
                options=[ALLOW_ONCE, ALWAYS_ALLOW, DENY],
                kind="permission",
            )
        )

        if answer == ALWAYS_ALLOW:
            self.store.grant(folder)
            return True
        if answer == ALLOW_ONCE:
            return True
        self._denied_folders.add(str(folder))
        self._any_denied = True
        return False

    async def check_command(self, tool_name: str, arguments) -> bool:
        """Approve a shell/code command, prompting if needed (turn-scoped).

        Same authority as folder access, so a denial anywhere this turn stops
        further command prompts too.
        """
        if tool_name in self._cmd_allowed:
            return True
        if tool_name in self._cmd_denied or self._any_denied:
            return False
        if self.asker is None:
            return False

        detail = str(arguments)
        if len(detail) > 800:
            detail = detail[:800] + " …"
        if self.sandbox == "docker":
            where = "in an isolated Docker sandbox (no access to your files)"
        else:
            where = "on your computer — NOT sandboxed (it can touch your files)"
        answer = await self.asker.ask(
            Question(
                text=f"Allow AG2 Assistant to run {tool_name} {where}?",
                detail=detail,
                options=[ALLOW_ONCE, ALWAYS_ALLOW_CMD, DENY],
                kind="permission",
            )
        )

        if answer == ALWAYS_ALLOW_CMD:
            self._cmd_allowed.add(tool_name)
            return True
        if answer == ALLOW_ONCE:
            return True
        self._cmd_denied.add(tool_name)
        self._any_denied = True
        return False

    # Management pass-throughs (for a future `ag2assistant permissions` CLI, etc.)
    def is_allowed(self, folder) -> bool:
        return self.store.is_allowed(folder)

    def grant(self, folder) -> None:
        self.store.grant(folder)

    def revoke(self, folder) -> bool:
        return self.store.revoke(folder)

    def granted_folders(self) -> list[str]:
        return self.store.granted_folders()


async def request_access(target: Path, store: PermissionStore, asker: Asker | None) -> bool:
    """Backwards-compatible shim — delegates to `PermissionManager.check`."""
    return await PermissionManager(store, asker).check(target)
