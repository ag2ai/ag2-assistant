"""How the Telegram adapter renders the router's outcomes, and how a tapped option
gets back to it (no network).

The adapter decides nothing here: it turns a `Choose` into option buttons, a `Reply`
into text, silence into a deleted placeholder — and hands a tapped token straight
back to the router.
"""

from types import SimpleNamespace

import assistant.channels.telegram as telegram_mod
from assistant.channels.router import (
    COMMANDS,
    Ack,
    Choose,
    Nothing,
    Option,
    Refuse,
    Reply,
    ToolCall,
    tool_trace,
)
from assistant.channels.telegram import TelegramChannel


def _telegram_channel(**kwargs):
    ch = TelegramChannel(token="fake-token", **kwargs)
    ch._bot_username = "ag2assistantbot"
    ch._bot_id = 999
    return ch


class _FakeMessage:
    """A Telegram message the adapter edits, replies to, or deletes."""

    _next_id = 100

    def __init__(self) -> None:
        _FakeMessage._next_id += 1
        self.message_id = _FakeMessage._next_id
        self.text: str | None = None
        self.markup = None
        self.replies: list[str] = []
        self.reply_markups: list[object] = []
        self.deleted = False
        self.reactions: list[str] = []

    async def set_reaction(self, reaction):
        self.reactions.append(reaction)

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


async def test_a_long_reply_fills_the_placeholder_then_continues_in_new_messages():
    ch = _telegram_channel(message_limit=15)
    placeholder = _FakeMessage()
    message = _FakeMessage()

    await ch._render(Reply("First part.\n\nSecond part.\n\nThird part."), placeholder, message)

    assert placeholder.text == "First part."
    assert message.replies == ["Second part.", "Third part."]


async def test_a_long_choice_puts_the_buttons_under_the_last_chunk():
    ch = _telegram_channel(message_limit=15)
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

    def __init__(self, outcome, paired=True, steering=False) -> None:
        self.outcome = outcome
        self.chosen: list[tuple] = []
        self.handled: list[object] = []
        self.answered: list[tuple] = []
        self._paired = paired
        self._steering = steering

    def steers(self, inbound) -> bool:
        return self._steering

    async def answer(self, inbound, inquiry, text):
        self.answered.append((inquiry, text))
        return self.outcome

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


class _FakeBot:
    """Hands back the message it sent, so the adapter can take it back later."""

    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.messages: list[_FakeMessage] = []
        # (chat_id, message_id) per message the adapter asked Telegram to remove.
        self.removed: list[tuple] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        message = _FakeMessage()
        self.sent.append((chat_id, text, reply_markup))
        self.messages.append(message)
        return message

    async def delete_message(self, chat_id, message_id):
        self.removed.append((chat_id, message_id))


def _fake_query(data, chat_type="private", chat_id=42, message_id=101):
    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(
            chat=SimpleNamespace(type=chat_type, PRIVATE="private", id=chat_id),
            message_id=message_id,
        ),
        from_user=SimpleNamespace(id=7, full_name="Test User", username="tester"),
        answer=_noop,
    )


async def _noop(*a, **kw):
    return None


def _tapping_channel(outcome, paired=True) -> tuple[TelegramChannel, _FakeBot]:
    """A channel whose bot records the taps' effects (no network)."""
    ch = _telegram_channel()
    ch._router = _RecordingRouter(outcome, paired=paired)
    bot = _FakeBot()
    ch._app = SimpleNamespace(bot=bot)
    return ch, bot


async def test_tapping_an_option_sends_its_token_to_the_router():
    ch, bot = _tapping_channel(Reply("Now talking to Home, in a new chat."))

    await ch._on_callback(SimpleNamespace(callback_query=_fake_query("acc:home")), None)

    inbound, token = ch._router.chosen[0]
    assert token == "home"
    assert (inbound.platform, inbound.chat_id, inbound.is_direct) == ("telegram", "42", True)
    assert [sent[:2] for sent in bot.sent] == [(42, "Now talking to Home, in a new chat.")]


async def test_tapping_an_option_that_says_nothing_sends_nothing():
    ch, bot = _tapping_channel(Nothing())

    await ch._on_callback(SimpleNamespace(callback_query=_fake_query("acc:home")), None)
    assert bot.sent == []


async def test_a_spent_picker_is_taken_down():
    """The buttons are a one-shot: once a token is chosen the message they are on is
    removed, so the same choice cannot be tapped twice."""
    ch, bot = _tapping_channel(Nothing())

    await ch._on_callback(
        SimpleNamespace(callback_query=_fake_query("acc:home", message_id=77)), None
    )

    assert bot.removed == [(42, 77)]


async def test_a_tap_without_its_message_is_ignored():
    """Telegram omits the message when the bot may not read it back. There is then no
    chat to place the tap in, so nothing is chosen and nothing is taken down."""
    ch, bot = _tapping_channel(Reply("switched"))
    query = _fake_query("acc:home")
    query.message = None

    await ch._on_callback(SimpleNamespace(callback_query=query), None)

    assert ch._router.chosen == []
    assert (bot.sent, bot.removed) == ([], [])


async def test_an_unpaired_reply_cannot_answer_a_running_question():
    """The answer path runs ahead of the turn machinery, so it has to be behind the
    pairing gate too — otherwise a stranger in a group answers for the user."""
    ch = _telegram_channel()
    ch._router = _RecordingRouter(Nothing(), paired=False)
    ch._pending.create("42")
    question = _FakeMessage()
    ch._questions[question.message_id] = ""

    message = _incoming("yes, delete it", reply_to=question)
    await ch._on_message(SimpleNamespace(message=message), None)

    assert ch._pending.is_awaiting("42") is True  # still waiting for the real answer
    assert message.replies == []  # and no placeholder was ever shown


async def test_an_unpaired_tap_reaches_neither_the_router_nor_the_button():
    """A tap discloses as much as a message: nothing is chosen, the picker is not
    spent, and the button is never even acknowledged (ADR 0021)."""
    ch, bot = _tapping_channel(Reply("switched"), paired=False)
    answered: list[bool] = []
    query = _fake_query("acc:home")
    query.answer = lambda: answered.append(True) or _noop()

    await ch._on_callback(SimpleNamespace(callback_query=query), None)

    assert ch._router.chosen == []
    assert answered == []
    assert bot.removed == []


# --- questions, mirrored and answered ---


def _asking_channel(outcome=Nothing()) -> tuple[TelegramChannel, _FakeBot]:
    ch = _telegram_channel()
    ch._router = _RecordingRouter(outcome)
    bot = _FakeBot()
    ch._app = SimpleNamespace(bot=bot)
    return ch, bot


def _context() -> SimpleNamespace:
    """The PTB context a handler is called with — only its bot is ever read."""
    return SimpleNamespace(bot=None)


def _incoming(text="hi", reply_to=None) -> _FakeMessage:
    """A plain direct message from the paired user, optionally replying to something."""
    message = _FakeMessage()
    message.text = text
    message.caption = None
    message.chat = SimpleNamespace(type="private", PRIVATE="private", id=42)
    message.from_user = SimpleNamespace(id=7, full_name="Test User", username="tester")
    message.reply_to_message = reply_to
    for attr in ("document", "photo", "audio", "voice", "video"):
        setattr(message, attr, None)
    return message


async def test_a_mirrored_question_is_shown_with_its_options():
    ch, bot = _asking_channel()
    question = Choose("Which table?", (Option("By the window", "answer:inq-1:0"),))

    await ch.ask("42", "inq-1", question)

    chat_id, text, markup = bot.sent[0]
    assert (chat_id, text) == (42, "Which table?")
    assert _callback_data(markup) == ["acc:answer:inq-1:0"]


async def test_a_question_answered_elsewhere_is_taken_back():
    ch, bot = _asking_channel()
    await ch.ask("42", "inq-1", Choose("Which table?", ()))

    await ch.retract("42", "inq-1")

    assert bot.messages[0].deleted is True
    assert ch._shown == {} and ch._questions == {}


async def test_taking_back_a_question_that_was_never_shown_does_nothing():
    ch, _ = _asking_channel()
    await ch.retract("42", "inq-9")


async def test_a_typed_message_no_longer_answers_an_open_question():
    """The hijack is gone: a question can arrive here from a turn this conversation
    never started, so an unrelated message must still be a message."""
    ch, _ = _asking_channel(Reply("the answer"))
    ch._pending.create("42")
    ch._questions[_FakeMessage().message_id] = ""

    await ch._on_message(SimpleNamespace(message=_incoming("what's the weather?")), _context())

    assert ch._pending.is_awaiting("42") is True  # the question is still open
    assert len(ch._router.handled) == 1  # and the message ran a turn of its own


async def test_replying_to_a_question_answers_the_turn_that_asked_it():
    ch, _ = _asking_channel()
    fut = ch._pending.create("42")
    question = _FakeMessage()
    ch._questions[question.message_id] = ""

    await ch._on_message(SimpleNamespace(message=_incoming("yes", reply_to=question)), None)

    assert fut.result() == "yes"
    assert ch._router.handled == []  # answering is not a turn of its own


async def test_replying_to_a_mirrored_question_goes_to_the_router():
    ch, _ = _asking_channel()
    await ch.ask("42", "inq-1", Choose("What time?", ()))
    question = ch._shown["inq-1"]

    await ch._on_message(SimpleNamespace(message=_incoming("eight", reply_to=question)), None)

    assert ch._router.answered == [("inq-1", "eight")]
    assert ch._router.handled == []


async def test_a_late_answer_is_told_so():
    ch, bot = _asking_channel(Refuse("That question was already answered."))
    await ch.ask("42", "inq-1", Choose("What time?", ()))
    question = ch._shown["inq-1"]

    await ch._on_message(SimpleNamespace(message=_incoming("eight", reply_to=question)), None)

    assert bot.sent[-1][:2] == (42, "That question was already answered.")


async def test_a_reply_to_anything_else_is_an_ordinary_message():
    ch, _ = _asking_channel(Reply("the answer"))

    message = _incoming("and this?", reply_to=_FakeMessage())
    await ch._on_message(SimpleNamespace(message=message), _context())

    assert len(ch._router.handled) == 1


# --- steering a running turn ---


async def test_a_steered_message_is_acknowledged_with_a_reaction_and_no_placeholder():
    """The running turn's answer lands in the first message's placeholder, so this
    one gets no second one — the reaction says it was taken."""
    ch, _ = _asking_channel(Ack())
    ch._router._steering = True

    message = _incoming("focus on 2026")
    await ch._on_message(SimpleNamespace(message=message), _context())

    assert message.reactions == [telegram_mod.FED_REACTION]
    assert message.replies == []


async def test_a_reaction_the_bot_may_not_add_is_not_an_error():
    """Some group configurations deny reactions; the message is still fed."""

    class _Unreactable(_FakeMessage):
        async def set_reaction(self, reaction):
            raise RuntimeError("not allowed to react")

    ch, _ = _asking_channel(Ack())
    ch._router._steering = True

    message = _Unreactable()
    message.text = "focus on 2026"
    message.caption = None
    message.chat = SimpleNamespace(type="private", PRIVATE="private", id=42)
    message.from_user = SimpleNamespace(id=7, full_name="Test User", username="tester")
    message.reply_to_message = None
    for attr in ("document", "photo", "audio", "voice", "video"):
        setattr(message, attr, None)

    await ch._on_message(SimpleNamespace(message=message), _context())

    assert len(ch._router.handled) == 1
    assert message.replies == []


async def test_a_turn_that_ended_before_the_feed_still_gets_its_answer_delivered():
    """The gate can be stale by a moment; when the router runs a turn after all, its
    reply is sent rather than swallowed for want of a placeholder."""
    ch, bot = _asking_channel(Reply("done"))
    ch._router._steering = True

    message = _incoming("focus on 2026")
    await ch._on_message(SimpleNamespace(message=message), _context())

    assert bot.sent[-1][:2] == (42, "done")
    assert message.reactions == []


async def test_an_ordinary_message_still_gets_its_placeholder():
    ch, _ = _asking_channel(Reply("the answer"))

    message = _incoming("what's the weather?")
    await ch._on_message(SimpleNamespace(message=message), _context())

    assert message.replies == [telegram_mod.WORKING_PLACEHOLDER]
    assert message.reactions == []


# --- the Tool trace in the placeholder ---


class _Clock:
    """A hand-wound clock, so the edit cadence is testable without waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _tracer(placeholder, clock=None) -> telegram_mod.TraceEditor:
    return telegram_mod.TraceEditor(placeholder, clock=clock or _Clock())


def _trace_text(*names: str, working: bool = False) -> str:
    """A trace as the router renders it — the adapter is given nothing else."""
    return tool_trace(tuple(ToolCall(name, {"path": "a.py"}) for name in names), working=working)


async def test_the_first_traced_call_lands_in_the_placeholder():
    placeholder = _FakeMessage()
    live = _trace_text("read_file", working=True)

    await _tracer(placeholder)(live)

    assert placeholder.text == live
    assert placeholder.replies == []  # no second message for a decoration


async def test_a_further_call_inside_the_throttle_window_is_not_shown():
    """Nothing paces an edit for us, and an edit storm would delay the answer itself."""
    placeholder = _FakeMessage()
    clock = _Clock()
    tracer = _tracer(placeholder, clock)

    await tracer("first")
    clock.now += telegram_mod.TRACE_INTERVAL / 2
    await tracer("second")

    assert placeholder.text == "first"


async def test_a_call_after_the_throttle_window_is_shown():
    placeholder = _FakeMessage()
    clock = _Clock()
    tracer = _tracer(placeholder, clock)

    await tracer("first")
    clock.now += telegram_mod.TRACE_INTERVAL * 2
    await tracer("second")

    assert placeholder.text == "second"


async def test_the_settled_trace_lands_however_much_was_skipped():
    """The trace left behind is never a stale one."""
    placeholder = _FakeMessage()
    tracer = _tracer(placeholder)

    settled = _trace_text("read_file", "write_file")

    await tracer(_trace_text("read_file", working=True))
    await tracer(settled, final=True)

    assert placeholder.text == settled


async def test_a_refused_trace_edit_is_swallowed():
    """A rate limit costs the trace, not the turn."""

    class _Unwritable(_FakeMessage):
        async def edit_text(self, text, reply_markup=None):
            raise RuntimeError("too many requests")

    tracer = _tracer(_Unwritable())
    await tracer(_trace_text("read_file", working=True))
    assert tracer.traced is False  # so the reply still edits into the placeholder


async def test_a_reply_arrives_as_its_own_message_beneath_a_trace():
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    message = _FakeMessage()
    tracer = _tracer(placeholder)
    await tracer(_trace_text("read_file"), final=True)

    await ch._render(Reply("the answer"), placeholder, message, traced=tracer.traced)

    assert placeholder.text == _trace_text("read_file")  # the record is kept
    assert message.replies == ["the answer"]


async def test_a_reply_still_edits_the_placeholder_when_nothing_was_traced():
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    message = _FakeMessage()

    await ch._render(Reply("the answer"), placeholder, message, traced=False)

    assert placeholder.text == "the answer"
    assert message.replies == []


async def test_a_silent_outcome_keeps_a_traced_placeholder():
    """A stopped turn's record of work is not litter to be cleared away."""
    ch = _telegram_channel()
    placeholder = _FakeMessage()
    await _tracer(placeholder)(_trace_text("read_file"), final=True)

    await ch._render(Nothing(), placeholder, _FakeMessage(), traced=True)

    assert placeholder.deleted is False
    assert placeholder.text == _trace_text("read_file")


async def test_a_long_reply_beneath_a_trace_is_still_split():
    ch = _telegram_channel(message_limit=15)
    placeholder = _FakeMessage()
    message = _FakeMessage()

    await ch._render(Reply("First part.\n\nSecond part."), placeholder, message, traced=True)

    assert message.replies == ["First part.", "Second part."]


async def test_a_turn_is_given_a_tracer_bound_to_its_placeholder():
    ch, _ = _asking_channel(Reply("the answer"))
    handed: list = []
    ch._router.handle = lambda inbound, **kw: handed.append(kw.get("progress")) or _noop()

    message = _incoming("what's in a.py?")
    await ch._on_message(SimpleNamespace(message=message), _context())

    assert isinstance(handed[0], telegram_mod.TraceEditor)


async def test_a_steering_message_is_given_no_tracer():
    """It has no placeholder of its own to grow — the running turn's trace is elsewhere."""
    ch, _ = _asking_channel(Ack())
    ch._router._steering = True
    handed: list = []
    ch._router.handle = lambda inbound, **kw: handed.append(kw.get("progress")) or _noop()

    await ch._on_message(SimpleNamespace(message=_incoming("focus on 2026")), _context())

    assert handed == [None]


async def test_a_settled_trace_that_will_not_land_gives_way_to_the_answer():
    """Better to lose the trace than to leave a finished turn reading as running."""

    class _Unwritable(_FakeMessage):
        def __init__(self) -> None:
            super().__init__()
            self.refuse = False

        async def edit_text(self, text, reply_markup=None):
            if self.refuse:
                raise RuntimeError("too many requests")
            await super().edit_text(text)

    ch = _telegram_channel()
    placeholder = _Unwritable()
    tracer = _tracer(placeholder)
    await tracer(_trace_text("read_file", working=True))
    placeholder.refuse = True
    await tracer(_trace_text("read_file"), final=True)

    assert tracer.traced is False
    placeholder.refuse = False
    await ch._render(Reply("the answer"), placeholder, _FakeMessage(), traced=tracer.traced)
    assert placeholder.text == "the answer"


async def test_a_trace_goes_out_exactly_as_it_was_rendered():
    """No parse mode and no Markdown pass, so a path with underscores in it survives."""
    placeholder = _FakeMessage()
    trace = tool_trace((ToolCall("read_file", {"path": "src/__init__.py"}),), working=True)

    await _tracer(placeholder)(trace)

    assert "src/__init__.py" in placeholder.text
