"""Tests for the channel layer — gating and Telegram normalization (no network)."""

from types import SimpleNamespace

import pytest

from assistant import cli
from assistant.channels.base import InboundMessage, should_respond
from assistant.channels.router import Choose, Reply
from assistant.channels.telegram import TelegramChannel
from assistant.connections import ConnectionStore
from assistant.pairing import PairingStore


def _msg(text="hi", is_direct=True, mentioned=False, has_attachment=False) -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id="u1",
        chat_id="c1",
        platform="telegram",
        is_direct=is_direct,
        mentioned=mentioned,
        has_attachment=has_attachment,
    )


def test_should_respond_dm():
    assert should_respond(_msg(is_direct=True)) is True


def test_should_respond_group_without_mention():
    assert should_respond(_msg(is_direct=False, mentioned=False)) is False


def test_should_respond_group_with_mention():
    assert should_respond(_msg(is_direct=False, mentioned=True)) is True


def test_should_respond_empty_text():
    assert should_respond(_msg(text="   ", is_direct=True)) is False


def test_should_respond_wordless_file_in_a_dm():
    """Dropping a file in with no caption is a message, not silence."""
    assert should_respond(_msg(text="", is_direct=True, has_attachment=True)) is True


def test_should_respond_wordless_file_in_a_group_still_needs_a_mention():
    """Gating is unchanged: a file is ignored in a group exactly as words would be."""
    assert should_respond(_msg(text="", is_direct=False, has_attachment=True)) is False
    assert should_respond(_msg(text="", is_direct=False, mentioned=True, has_attachment=True)) is (
        True
    )


# --- Telegram normalization (fake Update objects) ---


def _telegram_channel():

    ch = TelegramChannel(token="fake-token")
    ch._bot_username = "ag2assistantbot"
    ch._bot_id = 999
    return ch


def _fake_update(
    text, chat_type="private", chat_id=42, user_id=7, reply_to_bot=False, username="tester"
):
    chat = SimpleNamespace(type=chat_type, PRIVATE="private", id=chat_id)
    from_user = SimpleNamespace(id=user_id, full_name="Test User", username=username)
    reply_to = None
    if reply_to_bot:
        reply_to = SimpleNamespace(from_user=SimpleNamespace(id=999))
    message = SimpleNamespace(
        text=text,
        caption=None,
        chat=chat,
        from_user=from_user,
        reply_to_message=reply_to,
        document=None,
        photo=None,
        audio=None,
        voice=None,
        video=None,
    )
    return SimpleNamespace(message=message)


def test_normalize_dm():
    ch = _telegram_channel()
    inbound = ch._normalize(_fake_update("hello", chat_type="private"))
    assert inbound is not None
    assert inbound.is_direct is True
    assert inbound.mentioned is False
    assert inbound.text == "hello"
    assert (inbound.platform, inbound.chat_id) == ("telegram", "42")


def test_normalize_group_with_mention_strips_handle():
    ch = _telegram_channel()
    inbound = ch._normalize(_fake_update("@ag2assistantbot what is 2+2?", chat_type="supergroup"))
    assert inbound.is_direct is False
    assert inbound.mentioned is True
    assert "@ag2assistantbot" not in inbound.text
    assert inbound.text == "what is 2+2?"


def test_normalize_group_without_mention():
    ch = _telegram_channel()
    inbound = ch._normalize(_fake_update("just chatting", chat_type="group"))
    assert inbound.is_direct is False
    assert inbound.mentioned is False


def test_normalize_group_reply_to_bot_counts_as_mention():
    ch = _telegram_channel()
    inbound = ch._normalize(_fake_update("thanks!", chat_type="group", reply_to_bot=True))
    assert inbound.mentioned is True


def test_normalize_ignores_non_text():
    ch = _telegram_channel()
    msg = SimpleNamespace(
        text=None,
        caption=None,
        document=None,
        photo=None,
        audio=None,
        voice=None,
        video=None,
    )
    assert ch._normalize(SimpleNamespace(message=msg)) is None


def test_normalize_accepts_attachment_only_dm():
    """A photo with no caption is still a message to handle."""
    ch = _telegram_channel()
    update = _fake_update(None, chat_type="private")
    update.message.photo = [SimpleNamespace(file_id="abc")]
    inbound = ch._normalize(update)
    assert inbound is not None
    assert inbound.is_direct is True
    assert inbound.text == ""
    assert inbound.has_attachment is True


def test_normalize_marks_a_text_only_message_as_carrying_no_file():
    ch = _telegram_channel()
    assert ch._normalize(_fake_update("hello")).has_attachment is False


def test_telegram_requires_token():
    """The token is handed over by the caller; the adapter never reads the environment."""
    with pytest.raises(ValueError):
        TelegramChannel()


# --- the single-channel CLI commands run as a Connection, not as a platform string ---


def test_the_cli_runs_as_the_platforms_own_connection(paths):
    """`ag2-assistant telegram` must key by the real Connection id: pairing, Peers and
    the default Profile are all keyed by it, so a literal "telegram" reaches nobody."""
    cid, kwargs = cli._cli_connection("telegram", paths, {"TELEGRAM_BOT_TOKEN": "seed-tok"})

    assert cid == ConnectionStore(paths).connections_for("telegram")[0].id
    assert kwargs == {"token": "seed-tok"}


def test_the_cli_connection_serves_the_accounts_paired_to_it(paths):
    env = {"TELEGRAM_BOT_TOKEN": "seed-tok"}
    real = ConnectionStore(paths, env).connections_for("telegram")[0].id
    PairingStore(paths).add_account(real, "42", "telegram")

    assert (
        PairingStore(paths).is_paired(
            cli._cli_connection("telegram", paths, {"TELEGRAM_BOT_TOKEN": "seed-tok"})[0], "42"
        )
        is True
    )


def test_the_cli_runs_on_the_connections_stored_token_not_a_stray_env_one(paths):
    connection = ConnectionStore(paths).create_connection(
        "telegram", "Work", {"TELEGRAM_BOT_TOKEN": "stored"}
    )

    assert cli._cli_connection("telegram", paths, {"TELEGRAM_BOT_TOKEN": "stale-env"}) == (
        connection.id,
        {"token": "stored"},
    )


def test_the_cli_creates_a_connection_when_the_platform_has_none(paths):
    """A token exported into a container that has no Connection for it yet gets a real
    Connection, so pairing and Peers are never keyed by the platform string."""
    ConnectionStore(paths).create_connection("discord", "", {"DISCORD_BOT_TOKEN": "d"})

    cid, kwargs = cli._cli_connection("telegram", paths, {"TELEGRAM_BOT_TOKEN": "env-tok"})

    assert kwargs == {"token": "env-tok"}
    assert cid != "telegram"
    assert [c.id for c in ConnectionStore(paths).connections_for("telegram")] == [cid]
    assert cli._cli_connection("telegram", paths, {"TELEGRAM_BOT_TOKEN": "env-tok"})[0] == cid


def test_the_cli_hands_slack_both_of_its_tokens(paths):
    cid, kwargs = cli._cli_connection(
        "slack", paths, {"SLACK_BOT_TOKEN": "bot", "SLACK_APP_TOKEN": "app"}
    )

    assert cid == ConnectionStore(paths).connections_for("slack")[0].id
    assert kwargs == {"bot_token": "bot", "app_token": "app"}


# --- the router the single-channel CLI commands hand their adapter ---


class _OneGateway:
    """The single gateway a CLI command builds — enough of one for a turn to run."""

    def __init__(self, reply: str = "the answer") -> None:
        self.reply = reply
        self.calls: list[str] = []

    def is_running(self, chat_id: str = "default") -> bool:
        return False

    async def feed_message(self, text: str, chat_id: str = "default", attachments=None) -> bool:
        return False

    async def send_message(self, text, chat_id="default", **kw) -> str:
        self.calls.append(text)
        return self.reply


class _OneChannel:
    """The single adapter a CLI command starts — records what is pushed through it."""

    def __init__(self) -> None:
        self.notified: list[tuple[str, str]] = []
        self.asked: list[tuple[str, str]] = []
        self.retracted: list[tuple[str, str]] = []

    async def notify(self, chat_id: str, text: str) -> None:
        self.notified.append((chat_id, text))

    async def ask(self, chat_id: str, inquiry: str, question) -> None:
        self.asked.append((chat_id, inquiry))

    async def retract(self, chat_id: str, inquiry: str) -> None:
        self.retracted.append((chat_id, inquiry))


def _cli_inbound(cid: str, text="hi", platform="telegram") -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id="42",
        chat_id="c1",
        platform=platform,
        connection=cid,
        is_direct=True,
    )


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
async def test_the_cli_router_runs_a_message_on_the_one_gateway(paths, platform):
    """The router each single-channel command starts needs a real `ProfileDirectory`,
    not a callable returning the gateway: the first inbound message asks it which
    profiles are available, and a bare function has no answer."""
    cid = ConnectionStore(paths).create_connection(platform, "", {}).id
    PairingStore(paths).add_account(cid, "42", platform)
    gateway, channel = _OneGateway("4"), _OneChannel()

    router = cli._cli_router(gateway, cid, channel, paths)
    outcome = await router.handle(_cli_inbound(cid, "what is 2+2?", platform))

    assert outcome == Reply("4")
    assert gateway.calls == ["what is 2+2?"]


async def test_the_cli_router_pushes_back_through_the_one_channel(paths):
    """Task outcomes and mirrored questions reach the user through the single adapter
    the command started — there is no ProfileManager holding a Connection→adapter map."""
    cid = ConnectionStore(paths).create_connection("telegram", "", {}).id
    channel = _OneChannel()
    directory = cli._CliDirectory(_OneGateway(), cid, channel)

    await directory.notify_channel(cid, "c1", "done")
    await directory.ask_channel(cid, "c1", "inq-1", Choose("pick", ()))
    await directory.retract_channel(cid, "c1", "inq-1")

    assert (channel.notified, channel.asked, channel.retracted) == (
        [("c1", "done")],
        [("c1", "inq-1")],
        [("c1", "inq-1")],
    )


async def test_the_cli_channel_is_not_given_another_connections_traffic(paths):
    """The CLI runs one Connection; an outcome whose origin is a different one has no
    adapter here and must not be delivered down the only one there is."""
    channel = _OneChannel()
    directory = cli._CliDirectory(_OneGateway(), "cn-mine", channel)

    await directory.notify_channel("cn-other", "c1", "not yours")

    assert channel.notified == []
