"""Slack channel adapter (Socket Mode — no public URL needed).

Responds to all direct messages and to @mentions in channels. Uses Slack's
"mrkdwn" formatting and chunks long replies. Requires two tokens:
  - SLACK_BOT_TOKEN  (xoxb-…)  bot token, scopes: app_mentions:read, chat:write,
                                im:history, im:read
  - SLACK_APP_TOKEN  (xapp-…)  app-level token with connections:write (Socket Mode)
"""

import asyncio
import contextlib
import os
import re

from assistant.channels.base import Channel, InboundMessage, should_respond
from assistant.channels.formatting import markdown_to_slack, split_for_limit
from assistant.hitl.base import Asker, Question
from assistant.hitl.channel import PendingAsks

SLACK_LIMIT = 3500
_ASK_TIMEOUT = 300.0
_ACTION_RE = re.compile(r"ag2assistant_opt_\d+")
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


async def _download_attachments(event: dict, bot_token: str) -> list:
    """Download a Slack message's files and build AG2 multimodal inputs.

    Slack file URLs are private — they require an `Authorization: Bearer` header
    with the bot token, and the `files:read` scope.
    """
    import aiohttp

    from assistant.attachments import build_input

    files = event.get("files") or []
    if not files:
        return []

    headers = {"Authorization": f"Bearer {bot_token}"}
    inputs = []
    async with aiohttp.ClientSession(headers=headers) as session:
        for f in files:
            url = f.get("url_private_download") or f.get("url_private")
            if not url or (f.get("size") or 0) > _MAX_ATTACHMENT_BYTES:
                continue
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.read()
            except Exception:
                continue  # skip what we can't fetch; the text still goes through
            inp = build_input(data, f.get("name") or "file", f.get("mimetype"))
            if inp is not None:
                inputs.append(inp)
    return inputs


class SlackAsker:
    """Asks a question in a specific Slack channel and awaits the answer."""

    def __init__(self, client, channel_id: str, pending: PendingAsks) -> None:
        self._client = client
        self._channel_id = channel_id
        self._pending = pending

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        text = question.text
        if question.detail:
            text += f"\n\n{question.detail}"
        fut = self._pending.create(self._channel_id)
        if question.options:
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": opt[:75]},
                            "value": opt,
                            "action_id": f"ag2assistant_opt_{i}",
                        }
                        for i, opt in enumerate(question.options)
                    ],
                },
            ]
            await self._client.chat_postMessage(channel=self._channel_id, text=text, blocks=blocks)
        else:
            await self._client.chat_postMessage(channel=self._channel_id, text=text)
        try:
            return await asyncio.wait_for(fut, timeout=timeout or _ASK_TIMEOUT)
        finally:
            self._pending.discard(self._channel_id)


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
        self._pending = PendingAsks()

    def _asker_for(self, channel_id: str) -> Asker:
        return SlackAsker(self._app.client, channel_id, self._pending)

    async def start(self, gateway) -> None:
        from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
        from slack_bolt.app.async_app import AsyncApp

        self._gateway = gateway
        self._app = AsyncApp(token=self._bot_token)
        self._app.event("app_mention")(self._handle_app_mention)
        self._app.event("message")(self._handle_message)
        self._app.action(_ACTION_RE)(self._on_action)
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
        if not text and not event.get("files"):
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
        # ignore bots/edits/joins — but allow file_share, which carries uploads
        if event.get("bot_id"):
            return None
        subtype = event.get("subtype")
        if subtype and subtype != "file_share":
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

    async def _on_action(self, ack, body, action, client) -> None:
        """A HITL button was tapped — resolve the pending question."""
        await ack()
        channel = body.get("channel", {}).get("id")
        answer = action.get("value", "")
        if channel:
            self._pending.resolve(channel, answer)
            ts = body.get("message", {}).get("ts")
            if ts:
                with contextlib.suppress(Exception):
                    await client.chat_delete(channel=channel, ts=ts)

    async def _handle_app_mention(self, event, say, client) -> None:
        if self._route_pending_answer(event):
            return
        await self._respond(self._mention_inbound(event), say, client, event)

    async def _handle_message(self, event, say, client) -> None:
        if self._route_pending_answer(event):
            return
        await self._respond(self._dm_inbound(event), say, client, event)

    def _route_pending_answer(self, event: dict) -> bool:
        """If a question awaits a typed answer in this channel, resolve it."""
        channel = event.get("channel")
        text = event.get("text")
        if (
            channel
            and text
            and not event.get("bot_id")
            and event.get("user") != self._bot_user_id
            and self._pending.is_awaiting(channel)
        ):
            self._pending.resolve(channel, text)
            return True
        return False

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

        attachments = await _download_attachments(event, self._bot_token)
        text = inbound.text or ("Here is a file I'm sharing with you." if attachments else "")
        try:
            reply = await self._gateway.send_message(
                text,
                chat_id=inbound.stable_id(),
                asker=self._asker_for(channel) if channel else None,
                attachments=attachments,
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
