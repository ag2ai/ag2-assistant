"""Telegram channel adapter (long-polling, no public URL needed).

Responds to all direct messages; in groups it only responds when the bot is
@mentioned or replied to. Sends an immediate "working" placeholder and edits it
into the final reply, so there is visible feedback on every client (Telegram's
typing indicator isn't reliably rendered for bots on Desktop/Web).
"""

import contextlib
import os

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from agclaw.channels.base import Channel, InboundMessage, should_respond
from agclaw.channels.formatting import markdown_to_plain

WORKING_PLACEHOLDER = "⏳ Sorting that out…"


class TelegramChannel(Channel):
    platform = "telegram"

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self._token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN not set (env var or token= argument)."
            )
        self._app: Application | None = None
        self._gateway = None
        self._bot_username: str | None = None
        self._bot_id: int | None = None

    async def start(self, gateway) -> None:
        self._gateway = gateway
        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

        await self._app.initialize()
        me = await self._app.bot.get_me()
        self._bot_username = me.username
        self._bot_id = me.id
        await self._app.start()
        await self._app.updater.start_polling()

    def format_outbound(self, text: str) -> str:
        """Telegram renders raw Markdown literally, so send clean plain text."""
        return markdown_to_plain(text)

    async def stop(self) -> None:
        if self._app is None:
            return
        if self._app.updater and self._app.updater.running:
            await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        self._app = None

    def _normalize(self, update: Update) -> InboundMessage | None:
        msg = update.message
        if msg is None or msg.text is None:
            return None

        chat = msg.chat
        is_direct = chat.type == chat.PRIVATE
        text = msg.text

        mentioned = False
        if not is_direct and self._bot_username:
            handle = f"@{self._bot_username}"
            if handle.lower() in text.lower():
                mentioned = True
                text = text.replace(handle, "").strip()
            elif (
                msg.reply_to_message
                and msg.reply_to_message.from_user
                and msg.reply_to_message.from_user.id == self._bot_id
            ):
                mentioned = True

        return InboundMessage(
            text=text,
            sender_id=str(msg.from_user.id) if msg.from_user else "unknown",
            chat_id=str(chat.id),
            platform=self.platform,
            is_direct=is_direct,
            mentioned=mentioned,
            sender_name=msg.from_user.full_name if msg.from_user else None,
            raw=update,
        )

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        inbound = self._normalize(update)
        if inbound is None or not should_respond(inbound):
            return

        # Immediate, always-visible feedback: a placeholder we edit into the reply.
        placeholder = await update.message.reply_text(WORKING_PLACEHOLDER)

        try:
            reply = await self._gateway.send_message(
                inbound.text, session_id=inbound.session_id()
            )
        except Exception as exc:  # surface failures to the user
            reply = f"Sorry, something went wrong: {exc}"

        text = self.format_outbound(reply)
        try:
            await placeholder.edit_text(text)
        except Exception:
            # Edit can fail (e.g. reply too long to edit-in-place); fall back to a
            # fresh message so the user still gets the answer.
            with contextlib.suppress(Exception):
                await update.message.reply_text(text)
