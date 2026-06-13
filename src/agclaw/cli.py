"""AGClaw CLI."""

import asyncio

import typer

from agclaw.agent import ask
from agclaw.config import Config

app = typer.Typer(name="agclaw", help="AGClaw - Personal AI Assistant")


@app.command()
def agent(
    message: str = typer.Argument(help="Message to send to the agent"),
    memory: bool = typer.Option(True, help="Use the persistent user-profile memory."),
    platform: str = typer.Option("cli", help="Platform this session is on."),
    permissions: bool = typer.Option(
        True, help="Enable desktop permission/HITL prompts (browser popup)."
    ),
) -> None:
    """Send a message to the AGClaw agent."""
    config = Config()

    async def run() -> str:
        asker = None
        if permissions:
            from agclaw.hitl import DesktopAsker

            asker = DesktopAsker()
        try:
            return await ask(message, config, memory=memory, platform=platform, asker=asker)
        finally:
            if asker is not None:
                await asker.aclose()

    typer.echo(asyncio.run(run()))


profile_app = typer.Typer(help="Inspect or manage the learned user profile.")
app.add_typer(profile_app, name="profile")


@profile_app.command("show")
def profile_show() -> None:
    """Show the user profile AGClaw has learned so far."""
    from agclaw.memory import read_profile

    text = asyncio.run(read_profile())
    typer.echo(text or "(no profile learned yet)")


@profile_app.command("clear")
def profile_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete the learned user profile."""
    from agclaw.memory import clear_profile, default_store_path

    if not yes:
        confirm = typer.confirm(
            f"Delete the learned profile at {default_store_path()}?"
        )
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit()

    cleared = asyncio.run(clear_profile())
    typer.echo("Profile cleared." if cleared else "No profile to clear.")


@app.command()
def gateway(
    host: str = typer.Option("127.0.0.1", help="Host to bind."),
    port: int = typer.Option(8800, help="Port to bind."),
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Start the AGClaw gateway (REST + WebSocket API for UI clients)."""
    import uvicorn

    from agclaw.gateway.app import create_app

    typer.echo(f"AGClaw gateway starting on http://{host}:{port}")
    typer.echo(f"  POST http://{host}:{port}/api/message")
    typer.echo(f"  WS   ws://{host}:{port}/api/ws")
    uvicorn.run(create_app(memory=memory), host=host, port=port)


@app.command()
def telegram(
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Run AGClaw on Telegram (long-polling). Needs TELEGRAM_BOT_TOKEN in env/.env."""
    import asyncio

    from agclaw.channels import get_channel
    from agclaw.gateway.core import Gateway

    async def run() -> None:
        gateway = Gateway(memory=memory, platform="telegram")
        await gateway.start()
        channel = get_channel("telegram")
        await channel.start(gateway)
        typer.echo("AGClaw is live on Telegram. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await channel.stop()
            await gateway.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def discord(
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Run AGClaw on Discord. Needs DISCORD_BOT_TOKEN in env/.env and the
    Message Content Intent enabled in the Discord Developer Portal."""
    import asyncio

    from agclaw.channels import get_channel
    from agclaw.gateway.core import Gateway

    async def run() -> None:
        gateway = Gateway(memory=memory, platform="discord")
        await gateway.start()
        channel = get_channel("discord")
        await channel.start(gateway)
        typer.echo("AGClaw is live on Discord. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await channel.stop()
            await gateway.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def slack(
    memory: bool = typer.Option(True, help="Enable persistent user-profile memory."),
) -> None:
    """Run AGClaw on Slack (Socket Mode). Needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN."""
    import asyncio

    from agclaw.channels import get_channel
    from agclaw.gateway.core import Gateway

    async def run() -> None:
        gateway = Gateway(memory=memory, platform="slack")
        await gateway.start()
        channel = get_channel("slack")
        await channel.start(gateway)
        typer.echo("AGClaw is live on Slack. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await channel.stop()
            await gateway.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def version() -> None:
    """Show AGClaw version."""
    from agclaw import __version__

    typer.echo(f"agclaw {__version__}")


if __name__ == "__main__":
    app()
