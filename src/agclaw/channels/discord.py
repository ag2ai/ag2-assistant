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

from agclaw.channels.base import Channel, InboundMessage, should_respond
from agclaw.channels.formatting import split_for_limit

DISCORD_LIMIT = 2000


class DiscordChannel(Channel):
    platform = "discord"

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        if not self._token:
            raise ValueError(
                "DISCORD_BOT_TOKEN not set (env var or token= argument)."
            )
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._client.event(self.on_message)
        self._gateway = None
        self._task: asyncio.Task | None = None
        self._bot_user_id: int | None = None

    async def start(self, gateway) -> None:
        self._gateway = gateway
        # login() initialises the client (so wait_until_ready works), then
        # connect() runs the gateway loop as a background task.
        await self._client.login(self._token)
        self._task = asyncio.create_task(self._client.connect())
        await self._client.wait_until_ready()
        if self._client.user is not None:
            self._bot_user_id = self._client.user.id

    async def stop(self) -> None:
        await self._client.close()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    def _normalize(self, message: "discord.Message") -> InboundMessage | None:
        if message.author.bot:  # ignore bots (and ourselves) — avoids loops
            return None
        if not message.content:
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
            sender_name=getattr(message.author, "display_name", None),
            raw=message,
        )

    async def on_message(self, message: "discord.Message") -> None:
        inbound = self._normalize(message)
        if inbound is None or not should_respond(inbound):
            return

        async with message.channel.typing():
            try:
                reply = await self._gateway.send_message(
                    inbound.text, session_id=inbound.session_id()
                )
            except Exception as exc:  # surface failures to the user
                reply = f"Sorry, something went wrong: {exc}"

        for chunk in split_for_limit(self.format_outbound(reply), DISCORD_LIMIT):
            await message.channel.send(chunk)
