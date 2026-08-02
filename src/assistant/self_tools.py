"""Self-knowledge tools — let the agent report its own live state.

The `self-knowledge` skill is the static map (what exists, where the user goes to
change it). These are the live half: which folders this chat can actually reach,
what's connected, what model is active. Read-only — every mutation stays in
Settings or the existing action tools.

Wired in `create_agent`, not `build_system_tools`: those need a TaskService and so
exist only on the gateway, while "what can you do?" is asked on every surface.
"""

from ag2 import Context, tool

from assistant.permissions import PermissionManager


def _mode_label(mode: str | None) -> str:
    return {"read_write": "read + write", "read": "read only"}.get(mode or "", "no access")


def build_self_tools(config, settings) -> list:
    """Read-only tools over this agent's own state. `config` is the profile's
    Config (its `data_dir.name` is the persona id); `settings` is the profile's
    `Settings`."""

    @tool
    async def list_folders(context: Context) -> str:
        """Which folders you can read or write, and at what mode — the real answer to
        "can you open this?" or "why can't you read that file?". Access is an
        allowlist: no Folder, or no Grant to it, means no access. Call this before
        telling the user anything about folder access."""
        # The turn's PermissionManager carries this persona AND this chat's id, and
        # its mode_for() implements the union — reading folders.json directly here
        # would silently miss chat-scoped Grants.
        permissions = context.dependencies.get(PermissionManager) or PermissionManager()
        folders = permissions.folders
        try:
            views = folders.list_folders()
        except Exception as exc:
            return f"Could not read the Folder registry: {exc}"

        lines = []
        if permissions.workspace_dir is not None:
            lines.append(f"{permissions.workspace_dir} — read + write (your own workspace)")

        reachable = []
        for v in views:
            mode = folders.mode_for(v["path"], permissions.profile, permissions.chat_id)
            if mode is None:
                continue
            gone = "" if v["exists"] else "  [path no longer exists — can be repointed]"
            reachable.append(f"{v['name'] or v['path']} ({v['path']}) — {_mode_label(mode)}{gone}")
        lines.extend(reachable)

        ungranted = len(views) - len(reachable)
        note = (
            f"{ungranted} registered folder(s) have no Grant for this persona/chat."
            if ungranted
            else ""
        )
        route = (
            "Nothing is blocked — there is just no Grant. Ask for the path you need and "
            "the user can approve it for this chat or this persona, or they can add it "
            "in Settings → Folders."
        )
        if not lines:
            return "\n".join(x for x in ("No folders are reachable.", note, route) if x)
        out = "Reachable now:\n" + "\n".join(lines)
        if note:
            out += f"\n\n{note} {route}"
        return out

    @tool
    async def describe_integrations() -> str:
        """What's connected right now: the user's Google account and their MCP
        servers. Call this before claiming you can or can't reach their mail,
        calendar, or drive."""
        from assistant.integrations.google_auth import GoogleAuth

        google = GoogleAuth(config.paths)
        lines = []
        try:
            if not google.is_configured():
                lines.append(
                    "Google: not set up (no OAuth client). The user adds it in "
                    "Settings → Integrations."
                )
            elif not google.has_token():
                lines.append(
                    "Google: set up but not signed in — sign in at Settings → Integrations."
                )
            elif not google.libs_available():
                who = google.account_email() or "the user's account"
                lines.append(
                    f"Google: signed in as {who}, but the optional client libraries are "
                    f"NOT installed, so Gmail/Calendar/Drive tools are unavailable this "
                    f"run. Tell the user to run `{google.install_hint()}` and restart "
                    f"AG2 Assistant. Do not claim you can read their mail."
                )
            else:
                who = google.account_email() or "unknown account"
                lines.append(f"Google: connected as {who} (Gmail, Calendar, Drive read).")
        except Exception as exc:
            lines.append(f"Google: could not determine status ({exc}).")

        try:
            servers = settings.list_mcp_servers()
            if servers:
                names = ", ".join(s.get("name", "?") for s in servers)
                lines.append(f"MCP servers for this persona: {names}.")
            else:
                lines.append("MCP servers: none configured for this persona.")
        except Exception as exc:
            lines.append(f"MCP servers: could not read them ({exc}).")

        lines.append("Google sign-in is install-wide; MCP servers are per-persona.")
        return "\n".join(lines)

    @tool
    async def describe_settings() -> str:
        """Your current configuration: the active model (install-wide), and this
        persona's voice and focus areas. Call this instead of guessing which model
        you are or what you're focused on."""
        lines = [f"Persona: {config.data_dir.name}"]
        lines.append(f"Model: {config.llm.model or 'unset'} (provider: {config.llm.provider})")

        try:
            provider = settings.voice_provider()
            lines.append(f"Voice: {settings.get_voice(provider)} ({provider})")
        except Exception:
            pass
        try:
            focuses = settings.get_focuses()
            lines.append(f"Focus areas: {', '.join(focuses) if focuses else 'none set'}")
        except Exception:
            pass

        lines.append(f"Shell/code sandbox: {config.tools.sandbox}")
        lines.append(
            "The model is install-wide — switching persona will not change it. Voice, "
            "focus, MCP servers, skills, workspace, memory and folder access are "
            "per-persona."
        )
        return "\n".join(lines)

    return [list_folders, describe_integrations, describe_settings]
