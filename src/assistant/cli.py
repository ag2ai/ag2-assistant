"""AG2 Assistant CLI."""

import asyncio
import os
from pathlib import Path

import typer

from assistant.agent import ask
from assistant.config import Config

app = typer.Typer(
    name="ag2-assistant",
    help="AG2 Assistant - Personal AI Assistant",
)


@app.callback()
def _global_options(
    data_dir: str | None = typer.Option(
        None,
        "--data-dir",
        help="Override the install root — all settings, profiles and state live here "
        "(same as AG2ASSISTANT_DATA_DIR).",
    ),
) -> None:
    """Global options, applied before any command runs."""
    if data_dir:
        os.environ["AG2ASSISTANT_DATA_DIR"] = str(Path(data_dir).expanduser())


def _resolve_profile_config(profile: str | None) -> Config:
    """Resolve the derived config for a data-touching CLI command (§4.7).

    Mirrors ``chat``: catch UnknownProfile (zero profiles / bad id → §3.5 guidance)
    and ArchivedProfile, print a friendly message, and exit 1. Returns the profile's
    derived config so callers read profile-owned paths off ``config.data_dir``.
    """
    from assistant.gateway.profile_manager import (
        ArchivedProfile,
        UnknownProfile,
        resolve_active_profile,
    )

    try:
        _, config, _ = resolve_active_profile(profile)
    except ArchivedProfile as exc:
        typer.echo(f"profile '{exc}' is archived")
        raise typer.Exit(1)
    except UnknownProfile as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)
    return config


@app.command()
def agent(
    message: str = typer.Argument(help="Message to send to the agent"),
    memory: bool = typer.Option(True, help="Use the persistent user-profile memory."),
    platform: str = typer.Option("cli", help="Platform this session is on."),
    permissions: bool = typer.Option(
        True, help="Enable desktop permission/HITL prompts (browser popup)."
    ),
    sandbox: str | None = typer.Option(
        None,
        help="Execution sandbox: 'local' (host, approval-gated) or 'docker' "
        "(isolated container, no prompts). Overrides AG2ASSISTANT_SANDBOX.",
    ),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Profile id to run in (default: the active default)."
    ),
) -> None:
    """Send a message to the AG2 Assistant agent."""
    if sandbox:
        os.environ["AG2ASSISTANT_SANDBOX"] = sandbox
    config = _resolve_profile_config(profile)

    async def run() -> str:
        asker = None
        if permissions:
            from assistant.hitl import DesktopAsker

            asker = DesktopAsker()
        try:
            if asker is not None and memory:
                from assistant.onboarding import needs_onboarding, run_onboarding

                user_store_path = config.root_dir / "user.db"  # shared universal memory
                if await needs_onboarding(user_store_path):
                    typer.echo("First time here — a few quick questions (all skippable):")
                    await run_onboarding(asker, user_store_path)
            return await ask(message, config, memory=memory, platform=platform, asker=asker)
        finally:
            if asker is not None:
                await asker.aclose()

    typer.echo(asyncio.run(run()))


@app.command()
def onboard(
    force: bool = typer.Option(False, "--force", "-f", help="Re-run even if already onboarded."),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Profile id to onboard (default: the active default)."
    ),
) -> None:
    """Run the first-run onboarding interview (name, location, hours, style).

    Seeds the UNIVERSAL "who the user is" memory (``root_dir/user.db``), shared by
    every profile — so this is install-wide, not per-profile."""
    from assistant.hitl import DesktopAsker
    from assistant.onboarding import needs_onboarding, run_onboarding

    user_store_path = _resolve_profile_config(profile).root_dir / "user.db"

    async def run() -> None:
        if not force and not await needs_onboarding(user_store_path):
            typer.echo("Already onboarded (the universal profile exists). Use --force to redo.")
            return
        asker = DesktopAsker()
        try:
            answers = await run_onboarding(asker, user_store_path)
        finally:
            await asker.aclose()
        if answers:
            typer.echo("Thanks! Saved: " + ", ".join(sorted(answers)))
        else:
            typer.echo("All skipped — no problem, I'll learn as we go.")

    asyncio.run(run())


@app.command()
def chat(
    memory: bool = typer.Option(True, help="Use the persistent user-profile memory."),
    platform: str = typer.Option("cli", help="Platform tag for this session."),
    permissions: bool = typer.Option(
        True, help="Enable desktop permission/HITL prompts (browser popup)."
    ),
    sandbox: str | None = typer.Option(
        None, help="Execution sandbox: 'local' or 'docker'. Overrides AG2ASSISTANT_SANDBOX."
    ),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Profile id to chat in (default: the active default)."
    ),
) -> None:
    """Start an interactive, multi-turn chat with AG2 Assistant (type 'exit' to quit)."""
    if sandbox:
        os.environ["AG2ASSISTANT_SANDBOX"] = sandbox

    from assistant.gateway.core import Gateway
    from assistant.gateway.profile_manager import (
        ArchivedProfile,
        UnknownProfile,
        resolve_active_profile,
    )

    try:
        _, chat_config, factory = resolve_active_profile(profile)
    except ArchivedProfile as exc:
        typer.echo(f"profile '{exc}' is archived")
        raise typer.Exit(1)
    except UnknownProfile as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)

    async def main() -> None:
        asker = None
        if permissions:
            from assistant.hitl import DesktopAsker

            asker = DesktopAsker()
        gateway = Gateway(
            config=chat_config, memory=memory, platform=platform, config_factory=factory
        )
        await gateway.start()
        typer.echo("AG2 Assistant chat — type 'exit' (or Ctrl-D) to quit.\n")
        try:
            while True:
                try:
                    user = await asyncio.to_thread(input, "you> ")
                except EOFError:
                    break
                if user.strip().lower() in {"exit", "quit", ":q"}:
                    break
                if not user.strip():
                    continue
                reply = await gateway.send_message(user, chat_id="cli-chat", asker=asker)
                typer.echo(f"ag2-assistant> {reply}\n")
        finally:
            if asker is not None:
                await asker.aclose()
            await gateway.close()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    typer.echo("\nbye")


profile_app = typer.Typer(help="Inspect or manage the learned user profile.")
app.add_typer(profile_app, name="profile")


@profile_app.command("show")
def profile_show(
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Profile id to inspect (default: the active default)."
    ),
) -> None:
    """Show the user profile AG2 Assistant has learned so far."""
    from assistant.memory import read_profile

    store_path = _resolve_profile_config(profile).data_dir / "profile.db"
    text = asyncio.run(read_profile(store_path))
    typer.echo(text or "(no profile learned yet)")


@profile_app.command("clear")
def profile_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Profile id to clear (default: the active default)."
    ),
) -> None:
    """Delete the learned user profile."""
    from assistant.memory import clear_profile

    store_path = _resolve_profile_config(profile).data_dir / "profile.db"
    if not yes:
        confirm = typer.confirm(f"Delete the learned profile at {store_path}?")
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit()

    cleared = asyncio.run(clear_profile(store_path))
    typer.echo("Profile cleared." if cleared else "No profile to clear.")


profiles_app = typer.Typer(help="Manage assistant profiles (isolated workspaces).")
app.add_typer(profiles_app, name="profiles")


# Default accent for headless creation. The backend keeps no palette catalogue
# (ADR 0002) — the preset colours live in the frontend — so the CLI carries just
# this single fallback hex so `create` works with no --accent.
_DEFAULT_ACCENT = "#109e91"


@profiles_app.command("create")
def profiles_create(
    name: str = typer.Argument(help="Display name for the profile (its id is a slug of this)."),
    accent: str = typer.Option(
        _DEFAULT_ACCENT,
        "--accent",
        help=f"Accent colour as a #rrggbb hex (default {_DEFAULT_ACCENT}).",
    ),
) -> None:
    """Register a new profile (headless bootstrap, §3.5).

    Registry-only: writes the profiles.json entry and creates the profile dir; it does
    NOT boot a runtime (a later `run`/`chat` picks it up). The first profile created
    becomes the active default automatically.
    """
    from assistant import profiles

    try:
        meta = profiles.create_profile(name, accent)
    except ValueError as exc:
        typer.echo(f"error: {exc}")
        raise typer.Exit(1)

    profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    typer.echo(f"Created profile '{meta.id}':")
    typer.echo(f"  name      {meta.name}")
    typer.echo(f"  accent    {meta.accent}")
    typer.echo(f"  workspace {meta.workspace}")
    typer.echo(f"\n`ag2-assistant run` and `ag2-assistant chat -p {meta.id}` will use it.")


@profiles_app.command("list")
def profiles_list(
    show_all: bool = typer.Option(
        False, "--all", help="Include archived profiles (hidden by default)."
    ),
) -> None:
    """List registered profiles (active default marked with *)."""
    from assistant import profiles

    metas = profiles.list_profiles(include_archived=show_all)
    if not metas:
        typer.echo("(no profiles yet — create one with 'ag2-assistant profiles create <name>')")
        return

    active = profiles.load_registry().get("active_default")
    header = f"{'':1} {'id':16} {'name':20} {'accent':9} workspace"
    typer.echo(header)
    for meta in metas:
        mark = "*" if meta.id == active else " "
        name = meta.name + (" (archived)" if meta.archived else "")
        typer.echo(f"{mark:1} {meta.id:16} {name:20} {meta.accent:9} {meta.workspace}")


perms_app = typer.Typer(help="Manage command permissions (install-wide).")
app.add_typer(perms_app, name="permissions")


def _permissions_store():
    """The single install-wide PermissionStore (config.root_dir/permissions.json).

    Grants are global now — one store, not per-profile — so these commands work even
    with zero profiles (no profile resolution needed)."""
    from assistant.config import load_config
    from assistant.permissions import PermissionStore

    return PermissionStore(load_config().root_dir / "permissions.json")


@perms_app.command("list")
def permissions_list() -> None:
    """List allowed commands (folder access is 'ag2-assistant folders')."""
    commands = _permissions_store().granted_commands()
    typer.echo("Allowed commands:")
    typer.echo("\n".join(f"  ✓ {c}" for c in commands) or "  (none)")


@perms_app.command("allow-command")
def permissions_allow_command(
    rule: str = typer.Argument(
        help="Command rule: a bare tool (e.g. 'gmail_send') or a shell prefix rule "
        "(e.g. 'run_shell_command(git *)')."
    ),
) -> None:
    """Permanently allow a tool/command rule."""
    from assistant.permissions import command_rule, parse_command_rule

    try:
        tool, prefix = parse_command_rule(rule)
        canonical = command_rule(tool, prefix)
        # grant_command also rejects bare grants on shell tools (per-prefix only).
        _permissions_store().grant_command(canonical)
    except ValueError as exc:
        typer.echo(
            f"Cannot allow {rule!r}: {exc}. Use a bare tool name (gmail_send) or "
            "'tool(prefix *)' (e.g. \"run_shell_command(git *)\")."
        )
        raise typer.Exit(1)
    typer.echo(f"Allowed command: {canonical}")


@perms_app.command("revoke-command")
def permissions_revoke_command(
    rule: str = typer.Argument(help="Command rule to revoke (as shown by 'list')."),
) -> None:
    """Revoke a previously allowed command rule."""
    from assistant.permissions import command_rule, parse_command_rule

    try:
        canonical = command_rule(*parse_command_rule(rule))
    except ValueError:
        typer.echo(
            f"Not a valid rule: {rule!r}. Use a bare tool name (gmail_send) or "
            "'tool(prefix *)' (e.g. \"run_shell_command(git *)\")."
        )
        raise typer.Exit(1)
    ok = _permissions_store().revoke_command(canonical)
    typer.echo(f"Revoked command: {canonical}" if ok else f"Not in allow list: {canonical}")


folders_app = typer.Typer(help="Manage Folders + Grants (install-wide registry, ADR 0006).")
app.add_typer(folders_app, name="folders")


def _folder_store():
    from assistant.config import load_config
    from assistant.folders import FolderStore

    return FolderStore(load_config().root_dir / "folders.json")


@folders_app.command("list")
def folders_list() -> None:
    """List every Folder, its path, and its Grants."""
    views = _folder_store().list_folders()
    if not views:
        typer.echo("No folders registered.")
        return
    for v in views:
        badge = "" if v["exists"] else "  (path not found)"
        typer.echo(f"{v['id']}  {v['name']}  {v['path']}{badge}")
        for g in v["grants"]:
            scope = f"chat {g['chat_id']}" if g["chat_id"] else "profile"
            typer.echo(f"    {g['profile']} ({scope}): {g['mode']}")


@folders_app.command("add")
def folders_add(
    path: str = typer.Argument(help="Directory to register."),
    name: str = typer.Option("", help="Display name (default: the directory's basename)."),
) -> None:
    """Register a directory as a Folder."""
    from assistant.folders import DuplicatePath

    try:
        v = _folder_store().create_folder(path, name=name)
    except DuplicatePath as exc:
        typer.echo(f"Already registered as {exc.existing['name']!r} ({exc.existing['id']}).")
        raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)
    typer.echo(f"Added {v['id']}: {v['name']} -> {v['path']}")


@folders_app.command("rm")
def folders_rm(folder_id: str = typer.Argument(help="Folder id (see 'list').")) -> None:
    """Delete a Folder — revokes every Grant to it instantly."""
    if not _folder_store().delete_folder(folder_id):
        typer.echo(f"Unknown folder: {folder_id}")
        raise typer.Exit(1)
    typer.echo(f"Deleted {folder_id} (all grants revoked).")


@folders_app.command("grant")
def folders_grant(
    folder_id: str = typer.Argument(help="Folder id (see 'list')."),
    profile: str = typer.Argument(help="Profile id the Grant belongs to."),
    mode: str = typer.Option("read", help="read or read_write."),
    chat: str = typer.Option("", help="Chat id for a chat-scoped Grant (default: whole profile)."),
) -> None:
    """Grant a profile (or one chat) access to a Folder."""
    try:
        _folder_store().set_grant(folder_id, mode, profile=profile, chat_id=chat)
    except KeyError:
        typer.echo(f"Unknown folder: {folder_id}")
        raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)
    typer.echo(f"Granted {mode} on {folder_id} to {profile}" + (f" (chat {chat})" if chat else ""))


@folders_app.command("revoke")
def folders_revoke(
    folder_id: str = typer.Argument(help="Folder id."),
    profile: str = typer.Argument(help="Profile id."),
    chat: str = typer.Option("", help="Chat id of a chat-scoped Grant."),
) -> None:
    """Revoke one Grant."""
    if not _folder_store().revoke_grant(folder_id, profile=profile, chat_id=chat):
        typer.echo("No such grant.")
        raise typer.Exit(1)
    typer.echo("Revoked.")


google_app = typer.Typer(help="Manage the Google (Gmail/Calendar/Drive) integration.")
app.add_typer(google_app, name="google")


@google_app.command("login")
def google_login(
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the consent URL instead of opening a browser."
    ),
) -> None:
    """Authorise AG2 Assistant to access your Google account (opens a browser once)."""
    from assistant.integrations.google_auth import (
        credentials_path,
        is_configured,
        login,
    )

    if not is_configured():
        typer.echo(f"Missing OAuth client file at {credentials_path()}.")
        typer.echo(
            "Create a Desktop OAuth client in Google Cloud (enable Gmail/Calendar/"
            "Drive APIs), download its JSON, and save it to that path."
        )
        raise typer.Exit(1)
    try:
        email = login(open_browser=not no_browser)
    except Exception as exc:
        typer.echo(f"Login failed: {exc}")
        raise typer.Exit(1)
    typer.echo(f"Signed in to Google as {email}.")


@google_app.command("logout")
def google_logout() -> None:
    """Remove the stored Google token."""
    from assistant.integrations.google_auth import logout

    typer.echo("Signed out of Google." if logout() else "Not signed in.")


@google_app.command("status")
def google_status() -> None:
    """Show Google integration status."""
    from assistant.integrations.google_auth import has_token, is_configured

    typer.echo(f"OAuth client configured: {is_configured()}")
    typer.echo(f"Signed in: {has_token()}")


@app.command()
def gateway(
    host: str = typer.Option("127.0.0.1", help="Host to bind."),
    port: int = typer.Option(8800, help="Port to bind."),
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Start the AG2 Assistant gateway (REST + WebSocket API for UI clients).

    Serves every unarchived profile under ``/api/p/{pid}/…`` (create_app owns the
    ProfileManager lifecycle). Equivalent to ``run`` for the REST surface."""
    import uvicorn

    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    typer.echo(f"AG2 Assistant gateway starting on http://{host}:{port}")
    typer.echo(f"  Web UI  http://{host}:{port}/")
    typer.echo(f"  API     http://{host}:{port}/api/p/{{pid}}/…")
    uvicorn.run(create_app(ProfileManager(memory=memory)), host=host, port=port)


@app.command()
def telegram(
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Run AG2 Assistant on Telegram (long-polling). Needs TELEGRAM_BOT_TOKEN in env/.env."""
    import asyncio

    from assistant.channels import get_channel
    from assistant.gateway.core import build_gateway

    async def run() -> None:
        gateway, tasks = build_gateway(memory=memory, platform="telegram")
        await gateway.start()
        tasks.set_emitter(gateway.emit_event)
        tasks.set_gateway(gateway)  # run_task_now from the channel executes runs here
        # tools only; the scheduler runs in `ag2-assistant run`, not per channel
        await tasks.start(scheduler=False)
        channel = get_channel("telegram")
        await channel.start(gateway)
        typer.echo("AG2 Assistant is live on Telegram. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await channel.stop()
            await tasks.close()
            await gateway.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def discord(
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Run AG2 Assistant on Discord. Needs DISCORD_BOT_TOKEN in env/.env and the
    Message Content Intent enabled in the Discord Developer Portal."""
    import asyncio

    from assistant.channels import get_channel
    from assistant.gateway.core import build_gateway

    async def run() -> None:
        gateway, tasks = build_gateway(memory=memory, platform="discord")
        await gateway.start()
        tasks.set_emitter(gateway.emit_event)
        tasks.set_gateway(gateway)  # run_task_now from the channel executes runs here
        # tools only; the scheduler runs in `ag2-assistant run`, not per channel
        await tasks.start(scheduler=False)
        channel = get_channel("discord")
        await channel.start(gateway)
        typer.echo("AG2 Assistant is live on Discord. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await channel.stop()
            await tasks.close()
            await gateway.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def slack(
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Run AG2 Assistant on Slack (Socket Mode). Needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN."""
    import asyncio

    from assistant.channels import get_channel
    from assistant.gateway.core import build_gateway

    async def run() -> None:
        gateway, tasks = build_gateway(memory=memory, platform="slack")
        await gateway.start()
        tasks.set_emitter(gateway.emit_event)
        tasks.set_gateway(gateway)  # run_task_now from the channel executes runs here
        # tools only; the scheduler runs in `ag2-assistant run`, not per channel
        await tasks.start(scheduler=False)
        channel = get_channel("slack")
        await channel.start(gateway)
        typer.echo("AG2 Assistant is live on Slack. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await channel.stop()
            await tasks.close()
            await gateway.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def run(
    host: str = typer.Option("127.0.0.1", help="Host for the REST/WS gateway."),
    port: int = typer.Option(8800, help="Port for the REST/WS gateway."),
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
    rest: bool = typer.Option(True, help="Also serve the REST/WebSocket API."),
    sandbox: str | None = typer.Option(
        None, help="Execution sandbox: 'local' or 'docker'. Overrides AG2ASSISTANT_SANDBOX."
    ),
    data_dir: str | None = typer.Option(
        None, "--data-dir", help="Override the install root (same as the top-level --data-dir)."
    ),
) -> None:
    """Run everything in one process — the ProfileManager boots every unarchived
    profile (each with its own gateway, scheduler, and enabled channels), and the
    REST/WS API serves every profile under ``/api/p/{pid}/…``. The manager is built
    here and handed to ``create_app``, which owns its lifecycle (started in the app
    lifespan). Zero profiles is a legal state — the SPA shell + global routes serve,
    and ``/api/p/*`` 404s until the first profile is created (§3.5)."""
    if data_dir:
        os.environ["AG2ASSISTANT_DATA_DIR"] = str(Path(data_dir).expanduser())
    if sandbox:
        os.environ["AG2ASSISTANT_SANDBOX"] = sandbox

    import uvicorn

    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    manager = ProfileManager(memory=memory)

    if not rest:
        # Headless: run the manager directly (boot profiles + channels + schedulers),
        # no HTTP surface. Same lifecycle create_app would drive, minus the server.
        async def headless() -> None:
            await manager.start()
            for runtime in manager.runtimes():
                for platform in getattr(runtime, "channels", []):
                    typer.echo(f"  channel: {getattr(platform, 'platform', '?')} ({runtime.pid})")
            typer.echo("AG2 Assistant is running (no REST). Press Ctrl+C to stop.")
            try:
                await asyncio.Event().wait()
            finally:
                await manager.close()

        try:
            asyncio.run(headless())
        except KeyboardInterrupt:
            typer.echo("\nStopped.")
        return

    # REST path: create_app starts/stops the manager in its lifespan; uvicorn runs it.
    app = create_app(manager)
    typer.echo(f"  Web UI + REST/WS: http://{host}:{port}/")
    typer.echo("AG2 Assistant is running. Press Ctrl+C to stop.")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def version() -> None:
    """Show AG2 Assistant version."""
    from assistant import __version__

    typer.echo(f"ag2-assistant {__version__}")


# --- ChatGPT-subscription auth ("Sign in with ChatGPT") --------------------- #

auth_app = typer.Typer(name="auth", help="Sign in with a ChatGPT/Codex subscription (OpenAI).")
app.add_typer(auth_app, name="auth")


@auth_app.command("login")
def auth_login(
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the URL and paste the code (headless)."
    ),
) -> None:
    """Sign in with ChatGPT to run the assistant on your Codex/ChatGPT subscription.

    Unofficial and likely against OpenAI's Terms of Service — your account could be
    restricted. To use it, also set the provider to OpenAI in subscription mode:
    `AG2ASSISTANT_LLM_PROVIDER=openai AG2ASSISTANT_OPENAI_AUTH_MODE=subscription`.
    """
    from assistant import codex_auth

    typer.echo("⚠️  Unofficial — this uses your ChatGPT subscription in a way OpenAI")
    typer.echo("   does not officially support; your account could be rate-limited.\n")
    try:
        if no_browser:
            verifier, challenge = codex_auth.generate_pkce()
            import secrets as _secrets

            state = _secrets.token_urlsafe(24)
            url = codex_auth.build_authorize_url(challenge, state)
            typer.echo("Open this URL, sign in, then paste the `code` from the redirect URL:\n")
            typer.echo(url + "\n")
            code = typer.prompt("code").strip()
            codex_auth.exchange_code(code, verifier)
        else:
            typer.echo("Opening your browser to sign in with ChatGPT…")
            codex_auth.run_local_login()
    except codex_auth.CodexAuthError as exc:
        typer.echo(f"Sign-in failed: {exc}")
        raise typer.Exit(1) from None
    st = codex_auth.status()
    acct = st.get("account_id") or "unknown account"
    typer.echo(f"Signed in ✓ ({acct})")


@auth_app.command("logout")
def auth_logout() -> None:
    """Remove the stored ChatGPT-subscription tokens."""
    from assistant import codex_auth

    typer.echo("Signed out." if codex_auth.logout() else "Not signed in.")


@auth_app.command("status")
def auth_status() -> None:
    """Show whether you're signed in with ChatGPT."""
    from assistant import codex_auth

    st = codex_auth.status()
    if st.get("signed_in"):
        typer.echo(f"Signed in ✓ (account: {st.get('account_id') or 'unknown'})")
    else:
        typer.echo("Not signed in. Run `ag2-assistant auth login`.")


if __name__ == "__main__":
    app()
