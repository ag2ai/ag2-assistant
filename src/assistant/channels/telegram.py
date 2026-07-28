"""Telegram channel adapter (long-polling, no public URL needed).

Responds to all direct messages; in groups it only responds when the bot is
@mentioned or replied to. Sends an immediate "working" placeholder and edits it
into the final reply, so there is visible feedback on every client (Telegram's
typing indicator isn't reliably rendered for bots on Desktop/Web).
"""

import asyncio
import contextlib
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from assistant.attachments import build_input
from assistant.channels.base import Channel, InboundMessage
from assistant.channels.formatting import markdown_to_plain
from assistant.channels.router import ChannelRouter, spoken_text
from assistant.hitl.base import Asker, PendingGuard, Question
from assistant.hitl.channel import PendingAsks

WORKING_PLACEHOLDER = "⏳ Sorting that out…"
_CB_PREFIX = "acw:"  # callback_data namespace for option buttons
_ASK_TIMEOUT = 300.0
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # Telegram bot download cap is ~20 MB


def _has_attachment(msg) -> bool:
    return bool(msg.document or msg.photo or msg.audio or msg.voice or msg.video)


async def _download_attachments(msg, bot) -> list:
    """Download a Telegram message's media and build AG2 multimodal inputs."""
    # (file_id, filename, mime) for each supported attachment on the message.
    specs: list[tuple[str, str, str | None]] = []
    if msg.document:
        d = msg.document
        specs.append((d.file_id, d.file_name or "document", d.mime_type))
    if msg.photo:  # list of sizes — the last is the largest
        specs.append((msg.photo[-1].file_id, "photo.jpg", "image/jpeg"))
    if msg.audio:
        a = msg.audio
        specs.append((a.file_id, a.file_name or "audio.mp3", a.mime_type))
    if msg.voice:
        specs.append((msg.voice.file_id, "voice.ogg", "audio/ogg"))
    if msg.video:
        v = msg.video
        specs.append((v.file_id, v.file_name or "video.mp4", v.mime_type))

    inputs = []
    for file_id, filename, mime in specs:
        try:
            tg_file = await bot.get_file(file_id)
            if tg_file.file_size and tg_file.file_size > _MAX_ATTACHMENT_BYTES:
                continue
            data = bytes(await tg_file.download_as_bytearray())
        except Exception:
            continue  # skip anything we can't fetch; the text still goes through
        inp = build_input(data, filename, mime)
        if inp is not None:
            inputs.append(inp)
    return inputs


class TelegramAsker(PendingGuard):
    """Asks a question in a specific Telegram chat and awaits the answer."""

    def __init__(self, bot, chat_id: str, pending: PendingAsks) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._pending = pending

    async def ask(self, question: Question, timeout: float | None = None) -> str:
        fut = self._pending.create(self._chat_id)
        text = question.text
        if question.detail:
            text += f"\n\n{question.detail}"
        markup = None
        if question.options:
            markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(opt, callback_data=f"{_CB_PREFIX}{opt[:55]}")]
                    for opt in question.options
                ]
            )
        await self._bot.send_message(int(self._chat_id), text, reply_markup=markup)
        try:
            with self.pending_guard():
                return await asyncio.wait_for(fut, timeout=timeout or _ASK_TIMEOUT)
        finally:
            self._pending.discard(self._chat_id)


class TelegramChannel(Channel):
    platform = "telegram"

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self._token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set (env var or token= argument).")
        self._app: Application | None = None
        self._router: ChannelRouter | None = None
        self._bot_username: str | None = None
        self._bot_id: int | None = None
        self._pending = PendingAsks()

    async def start(self, router: ChannelRouter) -> None:
        self._router = router
        # concurrent_updates lets a button-tap (callback) be handled WHILE a
        # message handler is blocked awaiting that very answer — otherwise PTB
        # processes updates one-at-a-time and HITL deadlocks.
        self._app = Application.builder().token(self._token).concurrent_updates(True).build()
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.ATTACHMENT) & ~filters.COMMAND,
                self._on_message,
            )
        )

        await self._app.initialize()
        me = await self._app.bot.get_me()
        self._bot_username = me.username
        self._bot_id = me.id
        await self._app.start()
        await self._app.updater.start_polling()

    def _asker_for(self, chat_id: str) -> Asker:
        return TelegramAsker(self._app.bot, chat_id, self._pending)

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or not query.data:
            return
        await query.answer()
        chat_id = str(query.message.chat.id)
        if query.data.startswith(_CB_PREFIX):
            answer = query.data[len(_CB_PREFIX) :]
            self._pending.resolve(chat_id, answer)
            # The prompt is a transient modal — remove it once answered so it
            # doesn't linger below the reply.
            with contextlib.suppress(Exception):
                await query.message.delete()

    def format_outbound(self, text: str) -> str:
        """Telegram renders raw Markdown literally, so send clean plain text."""
        return markdown_to_plain(text)

    async def notify(self, chat_id: str, text: str) -> None:
        """Push a task-run outcome into a Telegram chat (no reply-to-edit here,
        this isn't a reply). Mirrors `_on_message`'s send path; that path never
        chunks long text, so neither does this."""
        await self._app.bot.send_message(int(chat_id), self.format_outbound(text))

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
        if msg is None:
            return None
        # Media messages carry their text in `caption`; pure attachments have none.
        text = msg.text or msg.caption or ""
        if not text and not _has_attachment(msg):
            return None

        chat = msg.chat
        is_direct = chat.type == chat.PRIVATE

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

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if msg is None:
            return

        # If a question is awaiting a typed answer in this chat, this message IS
        # the answer — resolve it instead of starting a new turn.
        chat_id = str(msg.chat.id)
        if msg.text and self._pending.is_awaiting(chat_id):
            self._pending.resolve(chat_id, msg.text)
            return

        inbound = self._normalize(update)
        if inbound is None or not self._router.accepts(inbound):
            return

        # Immediate, always-visible feedback: a placeholder we edit into the reply.
        placeholder = await update.message.reply_text(WORKING_PLACEHOLDER)

        attachments = await _download_attachments(msg, context.bot)
        outcome = await self._router.handle(
            inbound,
            asker=self._asker_for(chat_id),
            attachments=attachments,
        )
        spoken = spoken_text(outcome)
        if spoken is None:
            # Nothing to say — drop the placeholder rather than leave it "working".
            with contextlib.suppress(Exception):
                await placeholder.delete()
            return

        text = self.format_outbound(spoken)
        try:
            await placeholder.edit_text(text)
        except Exception:
            # Edit can fail (e.g. reply too long to edit-in-place); fall back to a
            # fresh message so the user still gets the answer.
            with contextlib.suppress(Exception):
                await update.message.reply_text(text)
