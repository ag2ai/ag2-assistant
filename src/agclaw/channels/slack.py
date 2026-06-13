"""Slack channel adapter (Socket Mode — no public URL needed).

Responds to all direct messages and to @mentions in channels. Uses Slack's
"mrkdwn" formatting and chunks long replies. Requires two tokens:
  - SLACK_BOT_TOKEN  (xoxb-…)  bot token, scopes: app_mentions:read, chat:write,
                                im:history, im:read
  - SLACK_APP_TOKEN  (xapp-…)  app-level token with connections:write (Socket Mode)
"""

import os
import re

from agclaw.channels.base import Channel, InboundMessage, should_respond
from agclaw.channels.formatting import markdown_to_slack, split_for_limit

SLACK_LIMIT = 3500


class SlackChannel(Channel):
    platform = "slack"

    def __init__(self, bot_token: str | None = None, app_token: str | None = None) -> None:
        self._bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self._app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        if not self._bot_token or not self._app_token:
            raise ValueError(
                "SLACK_BOT_TOKEN and SLACK_APP_TOKEN must both be set "
                "(env vars or constructor args)."
            )
        self._app = None
        self._handler = None
        self._gateway = None
        self._bot_user_id: str | None = None

    async def start(self, gateway) -> None:
        from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
        from slack_bolt.app.async_app import AsyncApp

        self._gateway = gateway
        self._app = AsyncApp(token=self._bot_token)
        self._app.event("app_mention")(self._handle_app_mention)
        self._app.event("message")(self._handle_message)
        self._handler = AsyncSocketModeHandler(self._app, self._app_token)

        auth = await self._app.client.auth_test()
        self._bot_user_id = auth["user_id"]
        await self._handler.connect_async()

    async def stop(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()
            self._handler = None

    def format_outbound(self, text: str) -> str:
        return markdown_to_slack(text)

    def _mention_inbound(self, event: dict) -> InboundMessage | None:
        text = re.sub(rf"<@{self._bot_user_id}>", "", event.get("text", "")).strip()
        if not text:
            return None
        return InboundMessage(
            text=text,
            sender_id=event.get("user", "unknown"),
            chat_id=event.get("channel", ""),
            platform=self.platform,
            is_direct=False,
            mentioned=True,
            raw=event,
        )

    def _dm_inbound(self, event: dict) -> InboundMessage | None:
        if event.get("channel_type") != "im":  # let app_mention handle channels
            return None
        if event.get("bot_id") or event.get("subtype"):  # ignore bots/edits/joins
            return None
        if event.get("user") == self._bot_user_id:  # ignore our own messages
            return None
        return InboundMessage(
            text=event.get("text", ""),
            sender_id=event.get("user", "unknown"),
            chat_id=event.get("channel", ""),
            platform=self.platform,
            is_direct=True,
            mentioned=False,
            raw=event,
        )

    async def _handle_app_mention(self, event, say, client) -> None:
        await self._respond(self._mention_inbound(event), say, client, event)

    async def _handle_message(self, event, say, client) -> None:
        await self._respond(self._dm_inbound(event), say, client, event)

    async def _respond(self, inbound: InboundMessage | None, say, client, event) -> None:
        if inbound is None or not should_respond(inbound):
            return

        # 👀 on the user's message while we work; removed once we've replied.
        channel = event.get("channel")
        ts = event.get("ts")
        reacted = False
        if channel and ts:
            try:
                await client.reactions_add(channel=channel, timestamp=ts, name="eyes")
                reacted = True
            except Exception:
                pass  # missing reactions:write or already reacted — non-fatal

        try:
            reply = await self._gateway.send_message(
                inbound.text, session_id=inbound.session_id()
            )
        except Exception as exc:  # surface failures to the user
            reply = f"Sorry, something went wrong: {exc}"

        for chunk in split_for_limit(self.format_outbound(reply), SLACK_LIMIT):
            await say(chunk)

        if reacted:
            try:
                await client.reactions_remove(channel=channel, timestamp=ts, name="eyes")
            except Exception:
                pass
