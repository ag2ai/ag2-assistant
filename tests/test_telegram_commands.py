"""How the Telegram adapter renders the router's outcomes, and how a tapped option
gets back to it (no network).

The adapter decides nothing here: it turns a `Choose` into option buttons, a `Reply`
into text, silence into a deleted placeholder — and hands a tapped token straight
back to the router.
"""

from types import SimpleNamespace

import assistant.channels.telegram as telegram_mod
from assistant.channels.router import COMMANDS, Choose, Nothing, Option, Reply
from assistant.channels.telegram import TelegramChannel


def _telegram_channel():
    ch = TelegramChannel(token="fake-token")
    ch._bot_username = "ag2assistantbot"
    ch._bot_id = 999
    return ch


class _FakeMessage:
    """A Telegram message the adapter edits, replies to, or deletes."""

    def __init__(self) -> None:
        self.text: str | None = None
        self.markup = None
        self.replies: list[str] = []
        self.reply_markups: list[object] = []
        self.deleted = False

    async def edit_text(self, text, reply_markup=None):
        self.text, self.markup = text, reply_markup

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.reply_markups.append(reply_markup)
        return _FakeMessage()

    async def delete(self):
        self.deleted = True


def _callback_data(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def test_a_choice_is_rendered_as_option_buttons():
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    choose = Choose("Which profile?", (Option("Work", "work"), Option("Home", "home")))

    await ch._render(choose, placeholder, _FakeMessage())

    assert placeholder.text == "Which profile?"
    assert _callback_data(placeholder.markup) == ["acc:work", "acc:home"]


async def test_a_choices_buttons_do_not_collide_with_a_questions():
    """Both are inline keyboards in the same chat; a tap must reach the one that
    sent it, so the two live in separate callback_data namespaces."""
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    await ch._render(Choose("pick", (Option("Work", "work"),)), placeholder, _FakeMessage())
    assert not _callback_data(placeholder.markup)[0].startswith(telegram_mod._CB_PREFIX)


async def test_a_reply_is_edited_into_the_placeholder_with_no_buttons():
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    message = _FakeMessage()
    await ch._render(Reply("the answer"), placeholder, message)
    assert (placeholder.text, placeholder.markup) == ("the answer", None)
    assert message.replies == []  # a short reply stays a single message


async def test_a_long_reply_fills_the_placeholder_then_continues_in_new_messages(monkeypatch):
    monkeypatch.setattr(telegram_mod, "TELEGRAM_LIMIT", 15)
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    message = _FakeMessage()

    await ch._render(Reply("First part.\n\nSecond part.\n\nThird part."), placeholder, message)

    assert placeholder.text == "First part."
    assert message.replies == ["Second part.", "Third part."]


async def test_a_long_choice_puts_the_buttons_under_the_last_chunk(monkeypatch):
    monkeypatch.setattr(telegram_mod, "TELEGRAM_LIMIT", 15)
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    message = _FakeMessage()

    await ch._render(
        Choose("First part.\n\nSecond part.", (Option("Work", "work"),)), placeholder, message
    )

    assert (placeholder.text, placeholder.markup) == ("First part.", None)
    assert message.replies == ["Second part."]
    assert _callback_data(message.reply_markups[-1]) == ["acc:work"]


async def test_a_silent_outcome_drops_the_placeholder():
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    await ch._render(Nothing(), placeholder, _FakeMessage())
    assert placeholder.deleted is True


async def test_a_failed_edit_still_delivers_the_choice():
    """Editing can fail; the user must still get the buttons."""

    class _Unwritable(_FakeMessage):
        async def edit_text(self, text, reply_markup=None):
            raise RuntimeError("cannot edit")

    ch = _telegram_channel()
    message = _FakeMessage()
    await ch._render(Choose("pick", (Option("Work", "work"),)), _Unwritable(), message)
    assert message.replies == ["pick"]


# --- the command menu ---


async def test_the_commands_are_registered_with_telegrams_own_menu():
    """So they're discoverable from the menu button, not only by typing."""
    registered: list = []
    ch = _telegram_channel()
    ch._app = SimpleNamespace(
        bot=SimpleNamespace(set_my_commands=lambda cmds: registered.append(cmds) or _noop())
    )

    await ch._publish_commands()

    assert registered[0] == [(c.name, c.description) for c in COMMANDS]


async def test_a_menu_that_will_not_register_does_not_stop_the_bot():
    def _boom(cmds):
        raise RuntimeError("telegram said no")

    ch = _telegram_channel()
    ch._app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=_boom))
    await ch._publish_commands()


# --- tapping an option ---


class _RecordingRouter:
    """Captures what the adapter asked the router to choose."""

    def __init__(self, outcome, paired=True) -> None:
        self.outcome = outcome
        self.chosen: list[tuple] = []
        self.handled: list[object] = []
        self._paired = paired

    def paired(self, inbound) -> bool:
        return self._paired

    async def choose(self, inbound, token):
        self.chosen.append((inbound, token))
        return self.outcome

    def accepts(self, inbound) -> bool:
        return self._paired

    async def handle(self, inbound, **kw):
        self.handled.append(inbound)
        return self.outcome


def _fake_query(data, chat_type="private", chat_id=42):
    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(
            chat=SimpleNamespace(type=chat_type, PRIVATE="private", id=chat_id),
            delete=_FakeMessage().delete,
        ),
        from_user=SimpleNamespace(id=7, full_name="Test User", username="tester"),
        answer=_noop,
    )


async def _noop(*a, **kw):
    return None


async def test_tapping_an_option_sends_its_token_to_the_router():
    ch = _telegram_channel()
    ch._router = _RecordingRouter(Reply("Now talking to Home, in a new chat."))
    sent: list[tuple] = []
    ch._app = SimpleNamespace(
        bot=SimpleNamespace(send_message=lambda cid, text: sent.append((cid, text)) or _noop())
    )

    await ch._on_callback(SimpleNamespace(callback_query=_fake_query("acc:home")), None)

    inbound, token = ch._router.chosen[0]
    assert token == "home"
    assert (inbound.platform, inbound.chat_id, inbound.is_direct) == ("telegram", "42", True)
    assert sent == [(42, "Now talking to Home, in a new chat.")]


async def test_tapping_an_option_that_says_nothing_sends_nothing():
    ch = _telegram_channel()
    ch._router = _RecordingRouter(Nothing())
    sent: list[tuple] = []
    ch._app = SimpleNamespace(
        bot=SimpleNamespace(send_message=lambda cid, text: sent.append((cid, text)) or _noop())
    )

    await ch._on_callback(SimpleNamespace(callback_query=_fake_query("acc:home")), None)
    assert sent == []


async def test_an_unpaired_message_cannot_answer_a_running_question():
    """The typed-answer path runs ahead of the turn machinery, so it has to be behind
    the pairing gate too — otherwise a stranger in a group answers for the user."""
    ch = _telegram_channel()
    ch._router = _RecordingRouter(Nothing(), paired=False)
    ch._pending.create("42")
    ch._app = SimpleNamespace(bot=SimpleNamespace(send_message=lambda cid, text: _noop()))

    message = _FakeMessage()
    message.text = "yes, delete it"
    message.caption = None
    message.chat = SimpleNamespace(type="private", PRIVATE="private", id=42)
    message.from_user = SimpleNamespace(id=7, full_name="Stranger", username="stranger")
    message.reply_to_message = None
    for attr in ("document", "photo", "audio", "voice", "video"):
        setattr(message, attr, None)

    await ch._on_message(SimpleNamespace(message=message), None)

    assert ch._pending.is_awaiting("42") is True  # still waiting for the real answer
    assert message.replies == []  # and no placeholder was ever shown


async def test_an_unpaired_tap_reaches_neither_the_router_nor_the_button():
    """A tap discloses as much as a message: nothing is chosen, the picker is not
    spent, and the button is never even acknowledged (ADR 0021)."""
    ch = _telegram_channel()
    ch._router = _RecordingRouter(Reply("switched"), paired=False)
    answered: list[bool] = []
    query = _fake_query("acc:home")
    query.answer = lambda: answered.append(True) or _noop()
    deleted = _FakeMessage()
    query.message.delete = deleted.delete

    await ch._on_callback(SimpleNamespace(callback_query=query), None)

    assert ch._router.chosen == []
    assert answered == []
    assert deleted.deleted is False
