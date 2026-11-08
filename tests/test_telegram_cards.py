"""How the Telegram adapter delivers a turn — its Markdown and its A2UI Cards as rich
messages — and what a tapped card button does, through the real router and agent
against a fake Bot API peer."""

import json
from types import SimpleNamespace

from assistant.channels.router import Reply
from assistant.channels.telegram import CARD_EXPIRED, TelegramChannel
from assistant.events import A2UISurface
from assistant.pairing import PairingStore
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager
from tests.support.fakes import FakeTelegramBot, ScriptedModels, telegram_tap

CATALOG = "https://ag2.ai/assistant/a2ui/catalog.json"


def _button(cid: str, label: str, name: str, context=None) -> list[dict]:
    event = {"name": name, "context": context or {}}
    return [
        {"id": cid, "component": "Button", "child": f"{cid}_label", "action": {"event": event}},
        {"id": f"{cid}_label", "component": "Text", "text": label},
    ]


def _drawing(text: str, *components, data=None) -> str:
    """A model reply saying `text` and drawing one surface of `components`."""
    messages = [
        {"version": "v1.0", "createSurface": {"surfaceId": "booking", "catalogId": CATALOG}},
        {
            "version": "v1.0",
            "updateComponents": {"surfaceId": "booking", "components": list(components)},
        },
    ]
    if data is not None:
        messages.append(
            {
                "version": "v1.0",
                "updateDataModel": {"surfaceId": "booking", "path": "/", "value": data},
            }
        )
    return f"{text}\n<a2ui-json>{json.dumps(messages)}</a2ui-json>"


BOOKING = _drawing(
    "Here is **the slot**.",
    {"id": "root", "component": "Column", "children": ["ask", "yes"]},
    {"id": "ask", "component": "Text", "text": "Book for 19:00?"},
    *_button("yes", "Book it", "book", {"time": "19:00"}),
)


class _Install:
    """A started profile paired with Telegram user 7, and the channels routing into it."""

    def __init__(self, paths, reply: str, bot: FakeTelegramBot) -> None:
        registry = ProfileRegistry(paths)
        profile = registry.create_profile("Channel", "#109e91")
        self.pid = profile.id
        registry.set_connection_default("telegram", profile.id)
        PairingStore(paths).add_account("telegram", "7", platform="telegram")
        self.models = ScriptedModels(script=lambda cfg, model: [reply])
        self.manager = make_manager(paths, persist=True, model_factory=self.models)
        self.bot = bot

    def channel(self) -> TelegramChannel:
        """A freshly started adapter, holding nothing from any earlier one."""
        ch = TelegramChannel(token="fake-token", connection="telegram")
        ch._app = SimpleNamespace(bot=self.bot)
        ch._router = self.manager.router
        return ch

    async def __aenter__(self):
        await self.manager.start()
        return self

    async def __aexit__(self, *exc):
        await self.manager.close()

    def asked(self) -> str:
        """The text of the last request the agent's model was sent."""
        return " ".join(
            str(getattr(part, "content", ""))
            for m in self.models.requests[-1][3]
            for part in getattr(m, "parts", ())
        )

    def card(self):
        """The (only) message holding a card's buttons."""
        return next(
            m
            for m in self.bot.messages
            if "tg-button" in self.bot.rich.get(m.message_id, {}).get("html", "")
            or self.bot.markups.get(m.message_id) is not None
        )

    def button(self) -> str:
        """The callback data of the card's first button, rich or plain."""
        card = self.card()
        rich = self.bot.rich.get(card.message_id)
        if rich is not None:
            return rich["html"].split('data="')[1].split('"')[0]
        return self.bot.markups[card.message_id].inline_keyboard[0][0].callback_data


async def _say(ch: TelegramChannel, bot: FakeTelegramBot, text: str, thread_id=None) -> None:
    message = bot.incoming(text)
    message.message_thread_id, message.is_topic_message = thread_id, thread_id is not None
    await ch._on_message(SimpleNamespace(message=message), SimpleNamespace(bot=bot))


async def test_a_reply_arrives_as_rich_markdown_and_its_card_as_rich_html(paths):
    async with _Install(paths, BOOKING, FakeTelegramBot()) as app:
        await _say(app.channel(), app.bot, "book?")

        shown = app.bot.shown()
        assert "Here is **the slot**." in shown
        # The card stands above the text that speaks of it, as in the web app.
        assert shown.index(app.bot.rich[app.card().message_id]["html"]) < shown.index(
            "Here is **the slot**."
        )
        card = app.bot.rich[app.card().message_id]["html"]
        assert "<p>Book for 19:00?</p>" in card
        assert len(app.button().encode()) <= 64


async def test_tapping_a_card_button_runs_its_action_as_a_turn(paths):
    async with _Install(paths, BOOKING, FakeTelegramBot()) as app:
        ch = app.channel()
        await _say(ch, app.bot, "book?")

        await ch._on_callback(telegram_tap(app.button(), app.card()), None)

        asked = app.asked()
        assert "[[A2UI_ACTION]]" in asked and "'book'" in asked and "19:00" in asked


async def test_a_card_still_answers_after_the_adapter_restarts(paths):
    async with _Install(paths, BOOKING, FakeTelegramBot()) as app:
        await _say(app.channel(), app.bot, "book?")

        await app.channel()._on_callback(telegram_tap(app.button(), app.card()), None)

        assert "'book'" in app.asked()


async def test_a_button_whose_card_is_not_in_the_chat_says_so(paths):
    async with _Install(paths, BOOKING, FakeTelegramBot()) as app:
        await _say(app.channel(), app.bot, "hello")
        before = len(app.models.requests)

        await app.channel()._on_callback(
            telegram_tap("a2u:000000000000:0", app.bot.messages[0]), None
        )

        assert app.bot.shown()[-1] == CARD_EXPIRED
        assert len(app.models.requests) == before


async def test_a_button_from_an_earlier_chat_does_not_act_in_the_new_one(paths):
    """Two chats may each draw a `booking` surface; a tap reaches only its own chat's."""
    async with _Install(paths, BOOKING, FakeTelegramBot()) as app:
        ch = app.channel()
        await _say(ch, app.bot, "book?")
        old_card, old_button = app.card(), app.button()
        await _say(ch, app.bot, "/new")
        await _say(ch, app.bot, "book again?")
        before = len(app.models.requests)

        await ch._on_callback(telegram_tap(old_button, old_card), None)

        assert app.bot.shown()[-1] == CARD_EXPIRED
        assert len(app.models.requests) == before


async def test_what_telegram_refuses_falls_back_to_plain_text_and_a_keyboard(paths):
    async with _Install(paths, BOOKING, FakeTelegramBot(refuse=True)) as app:
        ch = app.channel()
        await _say(ch, app.bot, "book?")

        assert app.bot.rich == {}
        assert "Here is the slot." in app.bot.shown()  # Markdown stripped for plain text
        assert app.card().text == "Book for 19:00?"

        await ch._on_callback(telegram_tap(app.button(), app.card()), None)

        assert "'book'" in app.asked()


async def test_a_card_too_big_for_one_rich_message_goes_as_text(paths):
    huge = _drawing("Big.", {"id": "root", "component": "Text", "text": "x" * 40_000})
    async with _Install(paths, huge, FakeTelegramBot()) as app:
        await _say(app.channel(), app.bot, "big?")

        plain = "".join(m.text or "" for m in app.bot.messages if m.message_id not in app.bot.rich)
        assert "x" * 40_000 in plain


async def test_a_reply_too_long_for_one_rich_message_goes_as_text(paths):
    async with _Install(paths, "y" * 40_000, FakeTelegramBot()) as app:
        await _say(app.channel(), app.bot, "long?")

        assert app.bot.rich == {}
        assert "".join(m.text or "" for m in app.bot.messages).count("y") == 40_000


async def test_a_card_lands_in_the_forum_topic_it_answers(paths):
    async with _Install(paths, BOOKING, FakeTelegramBot()) as app:
        await _say(app.channel(), app.bot, "book?", thread_id=9)

        assert app.card().message_thread_id == 9


async def test_a_server_action_redraws_its_card_in_place(paths):
    """A Card binds its text to its data model; saving new data redraws the same message."""
    status = A2UISurface(
        "status",
        component={
            "id": "root",
            "_components": [
                {"id": "root", "component": "Column", "children": ["state", "save"]},
                {"id": "state", "component": "Text", "text": {"path": "/status"}},
                *_button("save", "Confirm", "save_surface", {"data": {"path": "/next"}}),
            ],
        },
        data={"status": "open", "next": {"status": "booked"}},
    )
    async with _Install(paths, "Here.", FakeTelegramBot()) as app:
        ch = app.channel()
        await _say(ch, app.bot, "status?")
        gateway = app.manager.get(app.pid).require_gateway()
        (chat,) = await gateway.list_chats()
        await gateway.emit_event(chat["chat_id"], status)
        placeholder = await app.bot.send_message(42, "⏳")
        await ch._render(Reply("Here.", (status,), chat["chat_id"]), placeholder, placeholder)
        card = app.card()
        assert "<p>open</p>" in app.bot.rich[card.message_id]["html"]
        before = len(app.models.requests)

        await ch._on_callback(telegram_tap(app.button(), card), None)

        assert "<p>booked</p>" in app.bot.rich[card.message_id]["html"]
        assert len(app.models.requests) == before  # a server action runs no turn


async def test_a_task_notification_arrives_as_rich_markdown(paths):
    async with _Install(paths, BOOKING, FakeTelegramBot()) as app:
        await app.channel().notify("42", "Task **done**.")

        assert app.bot.shown() == ["Task **done**."]


async def test_a_card_whose_data_a_later_turn_wrote_keeps_its_buttons(paths):
    """A data-only record for a drawn surface updates its data and leaves its tree."""
    status = A2UISurface(
        "status",
        component={
            "id": "root",
            "_components": [
                {"id": "root", "component": "Column", "children": ["state", "save"]},
                {"id": "state", "component": "Text", "text": {"path": "/status"}},
                *_button("save", "Confirm", "save_surface", {"data": {"path": "/next"}}),
            ],
        },
        data={"status": "open", "next": {"status": "booked"}},
    )
    async with _Install(paths, "Here.", FakeTelegramBot()) as app:
        ch = app.channel()
        await _say(ch, app.bot, "status?")
        gateway = app.manager.get(app.pid).require_gateway()
        (chat,) = await gateway.list_chats()
        await gateway.emit_event(chat["chat_id"], status)
        placeholder = await app.bot.send_message(42, "⏳")
        await ch._render(Reply("Here.", (status,), chat["chat_id"]), placeholder, placeholder)
        card = app.card()
        written = {"status": "held", "next": {"status": "confirmed"}}
        await gateway.emit_event(chat["chat_id"], A2UISurface("status", data=written))

        await ch._on_callback(telegram_tap(app.button(), card), None)

        assert CARD_EXPIRED not in app.bot.shown()
        assert "<p>confirmed</p>" in app.bot.rich[card.message_id]["html"]
