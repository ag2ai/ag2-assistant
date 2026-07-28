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
from assistant.channels.formatting import markdown_to_plain, split_for_limit
from assistant.channels.router import COMMANDS, ChannelRouter, Choose, Outcome, spoken_text
from assistant.hitl.base import Asker, PendingGuard, Question
from assistant.hitl.channel import PendingAsks
from assistant.observability import log_suppressed

WORKING_PLACEHOLDER = "⏳ Sorting that out…"
TELEGRAM_LIMIT = 4096  # Telegram's per-message character cap
_CB_PREFIX = "acw:"  # callback_data namespace for HITL question buttons
_CHOICE_PREFIX = "acc:"  # callback_data namespace for router `Choose` option tokens
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

    def __init__(self, bot, chat_id: str, pending: PendingAsks, questions: dict) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._pending = pending
        self._questions = questions

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
        message = await self._bot.send_message(int(self._chat_id), text, reply_markup=markup)
        # A reply to this message is the answer; nothing else typed here is.
        self._questions[message.message_id] = ""
        try:
            with self.pending_guard():
                return await asyncio.wait_for(fut, timeout=timeout or _ASK_TIMEOUT)
        finally:
            self._questions.pop(message.message_id, None)
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
        # Message id of a question this bot has asked -> the Inquiry it resolves, or
        # "" for one a turn in that same chat is waiting on.
        self._questions: dict[int, str] = {}
        # Inquiry id -> the message showing it, so a resolution can take it back.
        self._shown: dict[str, object] = {}

    async def start(self, router: ChannelRouter) -> None:
        self._router = router
        # concurrent_updates lets a button-tap (callback) be handled WHILE a
        # message handler is blocked awaiting that very answer — otherwise PTB
        # processes updates one-at-a-time and HITL deadlocks.
        self._app = Application.builder().token(self._token).concurrent_updates(True).build()
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        # Commands are NOT excluded: the router owns the command surface (ADR 0019),
        # so `/profile` has to reach it rather than being dropped here.
        self._app.add_handler(MessageHandler(filters.TEXT | filters.ATTACHMENT, self._on_message))

        await self._app.initialize()
        me = await self._app.bot.get_me()
        self._bot_username = me.username
        self._bot_id = me.id
        await self._publish_commands()
        await self._app.start()
        await self._app.updater.start_polling()

    async def _publish_commands(self) -> None:
        """Put the router's commands in Telegram's own command menu, so they are
        discoverable without typing. Best-effort: the bot works without the menu."""
        try:
            await self._app.bot.set_my_commands([(c.name, c.description) for c in COMMANDS])
        except Exception as exc:
            log_suppressed("telegram command menu registration", exc)

    def _asker_for(self, chat_id: str) -> Asker:
        return TelegramAsker(self._app.bot, chat_id, self._pending, self._questions)

    async def _answer_unpaired(self, inbound: InboundMessage) -> None:
        """Run an unpaired account's message for its one possible effect — pairing.
        Anything else comes back as silence, which is sent as nothing at all."""
        spoken = spoken_text(await self._router.handle(inbound))
        if spoken:
            await self._send(inbound.chat_id, spoken)

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or not query.data:
            return
        # A tap is as much of a disclosure as a message: an unpaired account must not
        # answer a question, spend a picker, or see the button acknowledged.
        if not self._router.paired(self._from_callback(query)):
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
        elif query.data.startswith(_CHOICE_PREFIX):
            token = query.data[len(_CHOICE_PREFIX) :]
            outcome = await self._router.choose(self._from_callback(query), token)
            with contextlib.suppress(Exception):
                await query.message.delete()  # the picker is spent
            spoken = spoken_text(outcome)
            if spoken:
                await self._send(chat_id, spoken)

    def _from_callback(self, query) -> InboundMessage:
        """The Peer a button tap came from — enough for the router to place it, with
        no text of its own (the token carries the meaning)."""
        chat = query.message.chat
        return InboundMessage(
            text="",
            sender_id=str(query.from_user.id) if query.from_user else "unknown",
            chat_id=str(chat.id),
            platform=self.platform,
            is_direct=chat.type == chat.PRIVATE,
            mentioned=True,
            sender_name=query.from_user.full_name if query.from_user else None,
            sender_handle=query.from_user.username if query.from_user else None,
            raw=query,
        )

    def format_outbound(self, text: str) -> str:
        """Telegram renders raw Markdown literally, so send clean plain text."""
        return markdown_to_plain(text)

    async def _send(self, chat_id: str, text: str) -> None:
        """Send text to a chat as fresh message(s), rendered and within the size cap."""
        for chunk in split_for_limit(self.format_outbound(text), TELEGRAM_LIMIT):
            await self._app.bot.send_message(int(chat_id), chunk)

    async def notify(self, chat_id: str, text: str) -> None:
        """Push a task-run outcome into a Telegram chat — no placeholder to edit,
        this isn't a reply."""
        await self._send(chat_id, text)

    def _options_markup(self, question: Choose) -> InlineKeyboardMarkup | None:
        return (
            InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(opt.label, callback_data=f"{_CHOICE_PREFIX}{opt.token}")]
                    for opt in question.options
                ]
            )
            if question.options
            else None
        )

    async def ask(self, chat_id: str, inquiry: str, question: Choose) -> None:
        """Show a question mirrored from another surface, remembering the message so a
        reply to it answers it and a resolution elsewhere can take it back."""
        markup = self._options_markup(question)
        chunks = split_for_limit(self.format_outbound(question.text), TELEGRAM_LIMIT)
        message = None
        for index, chunk in enumerate(chunks):
            # Buttons belong under the whole question, so only the last chunk carries them.
            message = await self._app.bot.send_message(
                int(chat_id), chunk, reply_markup=markup if index == len(chunks) - 1 else None
            )
        if message is not None:
            self._questions[message.message_id] = inquiry
            self._shown[inquiry] = message

    async def retract(self, chat_id: str, inquiry: str) -> None:
        """Take back a mirrored question — it has been answered somewhere else."""
        message = self._shown.pop(inquiry, None)
        if message is None:
            return
        self._questions.pop(message.message_id, None)
        with contextlib.suppress(Exception):
            await message.delete()

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
        has_attachment = _has_attachment(msg)
        if not text and not has_attachment:
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
            has_attachment=has_attachment,
            sender_name=msg.from_user.full_name if msg.from_user else None,
            # The @handle a Paired-account invitation is matched against once, before
            # it pins to the numeric id above (ADR 0021).
            sender_handle=msg.from_user.username if msg.from_user else None,
            raw=update,
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if msg is None:
            return

        inbound = self._normalize(update)
        if inbound is None:
            return

        chat_id = str(msg.chat.id)
        if not self._router.paired(inbound):
            # Nothing an unpaired account sends may touch a running turn. Its message
            # goes to the router with no placeholder, in case it carries a code.
            await self._answer_unpaired(inbound)
            return

        # An answer is a reply to the question, never merely the next thing said: a
        # question can be mirrored here from a turn this conversation never started.
        if msg.reply_to_message is not None and await self._answer_question(inbound, msg):
            return

        if not self._router.accepts(inbound):
            return

        # Immediate, always-visible feedback: a placeholder we edit into the reply.
        placeholder = await update.message.reply_text(WORKING_PLACEHOLDER)

        attachments = await _download_attachments(msg, context.bot)
        outcome = await self._router.handle(
            inbound,
            asker=self._asker_for(chat_id),
            attachments=attachments,
        )
        await self._render(outcome, placeholder, update.message)

    async def _answer_question(self, inbound: InboundMessage, msg) -> bool:
        """Resolve the question this message replies to, if it replies to one. A
        mirrored question goes back through the router; a live one resolves here."""
        message_id = msg.reply_to_message.message_id
        inquiry = self._questions.get(message_id)
        if inquiry is None:
            return False
        text = msg.text or msg.caption or ""
        if not inquiry:
            self._questions.pop(message_id, None)
            self._pending.resolve(inbound.chat_id, text)
            return True
        spoken = spoken_text(await self._router.answer(inbound, inquiry, text))
        if spoken:
            await self._send(inbound.chat_id, spoken)
        return True

    async def _render(self, outcome: Outcome, placeholder, message) -> None:
        """Turn the router's outcome into Telegram: text edited into the placeholder,
        a choice as option buttons, silence as a deleted placeholder."""
        if isinstance(outcome, Choose):
            markup = self._options_markup(outcome)
            await self._say(self.format_outbound(outcome.text), placeholder, message, markup)
            return

        spoken = spoken_text(outcome)
        if spoken is None:
            # Nothing to say — drop the placeholder rather than leave it "working".
            with contextlib.suppress(Exception):
                await placeholder.delete()
            return
        await self._say(self.format_outbound(spoken), placeholder, message, None)

    async def _say(self, text: str, placeholder, message, markup) -> None:
        """Deliver text within Telegram's size cap: the first chunk edits the
        placeholder, the rest follow as new messages in order."""
        chunks = split_for_limit(text, TELEGRAM_LIMIT)
        for index, chunk in enumerate(chunks):
            # Buttons belong under the whole answer, so only the last chunk carries them.
            markup_for_chunk = markup if index == len(chunks) - 1 else None
            if index == 0:
                try:
                    await placeholder.edit_text(chunk, reply_markup=markup_for_chunk)
                    continue
                except Exception:
                    pass  # editing can fail; fall through to a fresh message
            with contextlib.suppress(Exception):
                await message.reply_text(chunk, reply_markup=markup_for_chunk)
