"""AG2 Assistant CLI."""

import asyncio
import os

import typer

from assistant.agent import ask
from assistant.config import load_config

app = typer.Typer(name="ag2assistant", help="AG2 Assistant - Personal AI Assistant")


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
) -> None:
    """Send a message to the AG2 Assistant agent."""
    if sandbox:
        os.environ["AG2ASSISTANT_SANDBOX"] = sandbox
    config = load_config()

    async def run() -> str:
        asker = None
        if permissions:
            from assistant.hitl import DesktopAsker

            asker = DesktopAsker()
        try:
            if asker is not None and memory:
                from assistant.onboarding import needs_onboarding, run_onboarding

                if await needs_onboarding():
                    typer.echo("First time here — a few quick questions (all skippable):")
                    await run_onboarding(asker)
            return await ask(message, config, memory=memory, platform=platform, asker=asker)
        finally:
            if asker is not None:
                await asker.aclose()

    typer.echo(asyncio.run(run()))


@app.command()
def onboard(
    force: bool = typer.Option(False, "--force", "-f", help="Re-run even if already onboarded."),
) -> None:
    """Run the first-run onboarding interview (name, location, hours, style)."""
    from assistant.hitl import DesktopAsker
    from assistant.onboarding import marker_path, needs_onboarding, run_onboarding

    async def run() -> None:
        if not force and not await needs_onboarding():
            typer.echo(f"Already onboarded (marker at {marker_path()}). Use --force to redo.")
            return
        asker = DesktopAsker()
        try:
            answers = await run_onboarding(asker)
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
) -> None:
    """Start an interactive, multi-turn chat with AG2 Assistant (type 'exit' to quit)."""
    if sandbox:
        os.environ["AG2ASSISTANT_SANDBOX"] = sandbox

    from assistant.gateway.core import Gateway

    async def main() -> None:
        asker = None
        if permissions:
            from assistant.hitl import DesktopAsker

            asker = DesktopAsker()
        gateway = Gateway(memory=memory, platform=platform)
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
                reply = await gateway.send_message(user, session_id="cli-chat", asker=asker)
                typer.echo(f"ag2assistant> {reply}\n")
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
def profile_show() -> None:
    """Show the user profile AG2 Assistant has learned so far."""
    from assistant.memory import read_profile

    text = asyncio.run(read_profile())
    typer.echo(text or "(no profile learned yet)")


@profile_app.command("clear")
def profile_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete the learned user profile."""
    from assistant.memory import clear_profile, default_store_path

    if not yes:
        confirm = typer.confirm(f"Delete the learned profile at {default_store_path()}?")
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit()

    cleared = asyncio.run(clear_profile())
    typer.echo("Profile cleared." if cleared else "No profile to clear.")


perms_app = typer.Typer(help="Manage folder access permissions.")
app.add_typer(perms_app, name="permissions")


@perms_app.command("list")
def permissions_list() -> None:
    """List granted and blocked folders."""
    from assistant.permissions import PermissionStore

    store = PermissionStore()
    granted = store.granted_folders()
    blocked = store.blocked_folders()
    typer.echo("Allowed folders:")
    typer.echo("\n".join(f"  ✓ {g}" for g in granted) or "  (none)")
    typer.echo("\nBlocked folders:")
    typer.echo("\n".join(f"  ✗ {b}" for b in blocked) or "  (none)")


@perms_app.command("allow")
def permissions_allow(folder: str = typer.Argument(help="Folder path to allow.")) -> None:
    """Permanently allow access to a folder."""
    from assistant.permissions import PermissionStore

    PermissionStore().grant(folder)
    typer.echo(f"Allowed: {folder}")


@perms_app.command("revoke")
def permissions_revoke(folder: str = typer.Argument(help="Folder path to revoke.")) -> None:
    """Revoke a previously granted folder."""
    from assistant.permissions import PermissionStore

    ok = PermissionStore().revoke(folder)
    typer.echo(f"Revoked: {folder}" if ok else f"Not in allow list: {folder}")


@perms_app.command("block")
def permissions_block(folder: str = typer.Argument(help="Folder path to block.")) -> None:
    """Permanently block a folder (the agent will never be allowed to access it)."""
    from assistant.permissions import PermissionStore

    PermissionStore().block(folder)
    typer.echo(f"Blocked: {folder}")


@perms_app.command("unblock")
def permissions_unblock(folder: str = typer.Argument(help="Folder path to unblock.")) -> None:
    """Remove a folder from the block list."""
    from assistant.permissions import PermissionStore

    ok = PermissionStore().unblock(folder)
    typer.echo(f"Unblocked: {folder}" if ok else f"Not in block list: {folder}")


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
    """Start the AG2 Assistant gateway (REST + WebSocket API for UI clients)."""
    import uvicorn

    from assistant.gateway.app import create_app

    typer.echo(f"AG2 Assistant gateway starting on http://{host}:{port}")
    typer.echo(f"  Web UI  http://{host}:{port}/")
    typer.echo(f"  POST    http://{host}:{port}/api/message")
    typer.echo(f"  WS      ws://{host}:{port}/api/stream")
    uvicorn.run(create_app(memory=memory), host=host, port=port)


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
        # tools only; the scheduler runs in `ag2assistant run`, not per channel
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
        # tools only; the scheduler runs in `ag2assistant run`, not per channel
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
        # tools only; the scheduler runs in `ag2assistant run`, not per channel
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
) -> None:
    """Run everything in one process — REST/WS gateway + every channel whose
    token is configured (Telegram/Discord/Slack), all sharing one agent."""
    if sandbox:
        os.environ["AG2ASSISTANT_SANDBOX"] = sandbox

    import uvicorn

    from assistant.channels import get_channel
    from assistant.gateway.app import create_app
    from assistant.gateway.core import build_gateway

    async def main() -> None:
        gateway, tasks = build_gateway(memory=memory, platform="multi")
        await gateway.start()
        tasks.set_emitter(gateway.emit_event)
        await tasks.start()  # task tools + scheduler, shared by channels and the web UI

        channels = []
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            channels.append(("telegram", get_channel("telegram")))
        if os.environ.get("DISCORD_BOT_TOKEN"):
            channels.append(("discord", get_channel("discord")))
        if os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_APP_TOKEN"):
            channels.append(("slack", get_channel("slack")))

        for name, ch in channels:
            await ch.start(gateway)
            typer.echo(f"  channel: {name}")

        server = None
        server_task = None
        if rest:
            config = uvicorn.Config(
                create_app(gateway=gateway, task_service=tasks),
                host=host,
                port=port,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            server_task = asyncio.create_task(server.serve())
            typer.echo(f"  Web UI + REST/WS: http://{host}:{port}/")

        typer.echo("AG2 Assistant is running. Press Ctrl+C to stop.")
        try:
            if server_task is not None:
                await server_task
            else:
                await asyncio.Event().wait()
        finally:
            for _, ch in channels:
                await ch.stop()
            if server is not None:
                server.should_exit = True
            await tasks.close()
            await gateway.close()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def version() -> None:
    """Show AG2 Assistant version."""
    from assistant import __version__

    typer.echo(f"ag2assistant {__version__}")


if __name__ == "__main__":
    app()
