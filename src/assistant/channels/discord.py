"""Discord channel adapter.

Responds to all direct messages; in servers it only responds when the bot is
@mentioned. Discord renders Markdown natively, so replies are sent as-is (only
chunked to the 2000-char limit). Discord's typing indicator is reliable, so we
use it for "working" feedback.

Requires the **Message Content Intent** to be enabled for the bot in the Discord
Developer Portal, otherwise message text arrives empty.
"""

import asyncio
import contextlib
import os
import re

import discord

from assistant.attachments import build_input
from assistant.channels.base import Channel, InboundMessage
from assistant.channels.formatting import split_for_limit
from assistant.channels.router import ChannelRouter, spoken_text
from assistant.hitl.base import Asker, PendingGuard, Question
from assistant.hitl.channel import PendingAsks

DISCORD_LIMIT = 2000
_ASK_TIMEOUT = 300.0
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


async def _download_attachments(message: "discord.Message") -> list:
    """Download a Discord message's attachments and build AG2 multimodal inputs."""
    inputs = []
    for att in message.attachments:
        if att.size and att.size > _MAX_ATTACHMENT_BYTES:
            continue
        try:
            data = await att.read()
        except Exception:
            continue  # skip what we can't fetch; the text still goes through
        inp = build_input(data, att.filename, att.content_type)
        if inp is not None:
            inputs.append(inp)
    return inputs


class _AskView(discord.ui.View):
    """Buttons for a HITL question; the first tap resolves and clears the prompt."""

    def __init__(self, options, channel_id: str, pending: PendingAsks, timeout: float):
        super().__init__(timeout=timeout)
        for opt in options:
            button = discord.ui.Button(label=opt[:80])
            button.callback = self._make_callback(opt, channel_id, pending)
            self.add_item(button)

    def _make_callback(self, option: str, channel_id: str, pending: PendingAsks):
        async def callback(interaction: discord.Interaction) -> None:
            pending.resolve(channel_id, option)
            with contextlib.suppress(Exception):
                await interaction.response.defer()
            with contextlib.suppress(Exception):
                await interaction.message.delete()  # transient prompt

        return callback


class DiscordAsker(PendingGuard):
    """Asks a question in a specific Discord channel and awaits the answer."""

    def __init__(self, client, channel_id: str, pending: PendingAsks) -> None:
        self._client = client
        self._channel_id = channel_id
        self._pending = pending

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        channel = self._client.get_channel(int(self._channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(self._channel_id))
        text = question.text
        if question.detail:
            text += f"\n\n{question.detail}"
        fut = self._pending.create(self._channel_id)
        if question.options:
            view = _AskView(
                question.options, self._channel_id, self._pending, timeout or _ASK_TIMEOUT
            )
            await channel.send(text, view=view)
        else:
            await channel.send(text)
        try:
            with self.pending_guard():
                return await asyncio.wait_for(fut, timeout=timeout or _ASK_TIMEOUT)
        finally:
            self._pending.discard(self._channel_id)


class DiscordChannel(Channel):
    platform = "discord"

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        if not self._token:
            raise ValueError("DISCORD_BOT_TOKEN not set (env var or token= argument).")
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._client.event(self.on_message)
        self._router: ChannelRouter | None = None
        self._task: asyncio.Task | None = None
        self._bot_user_id: int | None = None
        self._pending = PendingAsks()

    def _asker_for(self, channel_id: str) -> Asker:
        return DiscordAsker(self._client, channel_id, self._pending)

    async def start(self, router: ChannelRouter) -> None:
        self._router = router
        # login() initialises the client (so wait_until_ready works), then
        # connect() runs the gateway loop as a background task.
        await self._client.login(self._token)
        self._task = asyncio.create_task(self._client.connect())
        await self._client.wait_until_ready()
        if self._client.user is not None:
            self._bot_user_id = self._client.user.id

    async def notify(self, chat_id: str, text: str) -> None:
        """Push a task-run outcome into a Discord channel. Mirrors `on_message`'s
        send path: same `split_for_limit`/`DISCORD_LIMIT` chunking and
        `format_outbound`, but resolves the channel object ourselves since there's
        no inbound `message.channel` to reuse here."""
        channel = self._client.get_channel(int(chat_id)) or await self._client.fetch_channel(
            int(chat_id)
        )
        for chunk in split_for_limit(self.format_outbound(text), DISCORD_LIMIT):
            await channel.send(chunk)

    async def stop(self) -> None:
        await self._client.close()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    def _normalize(self, message: "discord.Message") -> InboundMessage | None:
        if message.author.bot:  # ignore bots (and ourselves) — avoids loops
            return None
        if not message.content and not message.attachments:
            return None

        is_direct = message.guild is None
        text = message.content

        mentioned = False
        if not is_direct and self._bot_user_id is not None:
            if any(u.id == self._bot_user_id for u in message.mentions):
                mentioned = True
                # Strip the bot mention tokens (<@id> / <@!id>).
                text = re.sub(rf"<@!?{self._bot_user_id}>", "", text).strip()

        return InboundMessage(
            text=text,
            sender_id=str(message.author.id),
            chat_id=str(message.channel.id),
            platform=self.platform,
            is_direct=is_direct,
            mentioned=mentioned,
            has_attachment=bool(message.attachments),
            sender_name=getattr(message.author, "display_name", None),
            raw=message,
        )

    async def on_message(self, message: "discord.Message") -> None:
        # If a question is awaiting a typed answer in this channel, this message
        # IS the answer — resolve it instead of starting a new turn.
        if message.author.bot:
            return
        channel_id = str(message.channel.id)
        if message.content and self._pending.is_awaiting(channel_id):
            self._pending.resolve(channel_id, message.content)
            return

        inbound = self._normalize(message)
        if inbound is None or not self._router.accepts(inbound):
            return

        async with message.channel.typing():
            attachments = await _download_attachments(message)
            outcome = await self._router.handle(
                inbound,
                asker=self._asker_for(channel_id),
                attachments=attachments,
            )

        spoken = spoken_text(outcome)
        if spoken is None:
            return
        for chunk in split_for_limit(self.format_outbound(spoken), DISCORD_LIMIT):
            await message.channel.send(chunk)
