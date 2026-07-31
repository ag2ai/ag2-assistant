"""The Peer registry — one conversation on the platform side, and what it remembers.

A Peer is keyed by (Connection, platform chat id) and holds the Profile that
conversation talks to. It is install-level state (ADR 0019): it spans Profiles by
construction, so it cannot live inside any one of them.
"""

import re

from assistant import peers


def test_an_unknown_conversation_has_no_peer_yet():
    assert peers.get_peer("cn-work", "42") is None


def test_the_selected_profile_survives_a_restart():
    """Nothing is cached in the process — a fresh read sees the same selection."""
    peers.select_profile("cn-work", "42", "work")
    assert peers.get_peer("cn-work", "42").profile == "work"


def test_two_conversations_hold_two_different_profiles():
    peers.select_profile("cn-work", "42", "work")
    peers.select_profile("cn-work", "99", "home")
    assert peers.get_peer("cn-work", "42").profile == "work"
    assert peers.get_peer("cn-work", "99").profile == "home"


def test_the_same_chat_id_on_two_connections_is_two_peers():
    """A Telegram DM's chat id is the user's own id, the same across two bots — so the
    Connection is what keeps the two conversations apart."""
    peers.select_profile("cn-work", "42", "work", platform="telegram")
    peers.select_profile("cn-play", "42", "home", platform="telegram")
    assert peers.get_peer("cn-work", "42").profile == "work"
    assert peers.get_peer("cn-play", "42").profile == "home"


def test_two_connections_on_one_chat_id_start_two_chats():
    first = peers.start_chat("cn-work", "42", platform="telegram")
    second = peers.start_chat("cn-play", "42", platform="telegram")
    assert first != second
    assert peers.get_peer("cn-work", "42").chat == first
    assert peers.get_peer("cn-play", "42").chat == second


def test_a_peer_remembers_which_surface_it_is():
    peers.select_profile("cn-work", "42", "work", surface="group")
    assert peers.get_peer("cn-work", "42").surface == "group"


def test_a_broken_registry_reads_as_no_peers(tmp_path, monkeypatch):
    monkeypatch.setattr(peers, "_path", lambda: tmp_path / "peers.json")
    (tmp_path / "peers.json").write_text("{ not json")
    assert peers.get_peer("cn-work", "42") is None


# --- the Chat a Peer is attached to ---


def test_a_peer_starts_attached_to_nothing():
    """The Chat is materialised by the first message, not by the selection."""
    assert peers.select_profile("cn-work", "42", "work").chat is None


def test_a_started_chat_is_opaque_not_the_platform_address():
    """A Chat id no longer doubles as an address, so a Peer can own more than one."""
    peers.select_profile("cn-work", "42", "work", platform="telegram")
    chat = peers.start_chat("cn-work", "42", platform="telegram")
    assert re.fullmatch(r"telegram-[0-9a-f]{8}", chat)


def test_starting_a_chat_attaches_the_peer_to_it():
    peers.select_profile("cn-work", "42", "work")
    chat = peers.start_chat("cn-work", "42")
    assert peers.get_peer("cn-work", "42").chat == chat


def test_two_started_chats_are_different_chats():
    peers.select_profile("cn-work", "42", "work")
    first = peers.start_chat("cn-work", "42")
    assert peers.start_chat("cn-work", "42") != first


def test_starting_a_chat_records_a_conversation_that_has_chosen_nothing():
    """A Peer riding the Channel default has no selection of its own, and still owns
    the Chats it starts."""
    chat = peers.start_chat("cn-work", "42")
    peer = peers.get_peer("cn-work", "42")
    assert (peer.profile, peer.chat) == (None, chat)


def test_switching_profile_leaves_the_peer_attached_to_nothing():
    """A Chat cannot cross Profiles, so the next message starts a fresh one."""
    peers.select_profile("cn-work", "42", "work")
    peers.start_chat("cn-work", "42")
    assert peers.select_profile("cn-work", "42", "home").chat is None


def test_reselecting_the_same_profile_keeps_the_attachment():
    peers.select_profile("cn-work", "42", "work")
    chat = peers.start_chat("cn-work", "42")
    assert peers.select_profile("cn-work", "42", "work").chat == chat


def test_attaching_to_an_existing_chat_moves_the_peer_without_starting_one():
    """Attaching is pure navigation — nothing is minted, nothing is left behind."""
    peers.select_profile("cn-work", "42", "work")
    started = peers.start_chat("cn-work", "42")
    peers.attach("cn-work", "42", "web-abc123")
    peer = peers.get_peer("cn-work", "42")
    assert (peer.chat, peer.chats) == ("web-abc123", [started, "web-abc123"])


def test_attaching_back_to_a_chat_records_it_once():
    started = peers.start_chat("cn-work", "42")
    peers.attach("cn-work", "42", "web-abc123")
    peers.attach("cn-work", "42", started)
    assert peers.get_peer("cn-work", "42").chats == [started, "web-abc123"]


# --- which Peer a Chat came from ---


def test_the_peer_a_chat_was_started_from_is_recoverable():
    """That is how a task pushes its outcome back to the conversation it came from."""
    chat = peers.start_chat("cn-work", "42", platform="telegram")
    peer = peers.peer_for_chat(chat)
    assert (peer.connection, peer.chat_id) == ("cn-work", "42")


def test_a_chat_stays_with_its_peer_after_the_peer_has_moved_on():
    chat = peers.start_chat("cn-work", "42")
    peers.start_chat("cn-work", "42")
    assert peers.peer_for_chat(chat).chat_id == "42"


def test_a_chat_nobody_started_belongs_to_no_peer():
    peers.start_chat("cn-work", "42")
    assert peers.peer_for_chat("web-abc123") is None


def test_forgetting_a_chat_drops_it_from_its_peer():
    """A deleted Chat is gone: neither attached nor owned."""
    chat = peers.start_chat("cn-work", "42")
    peers.forget_chat(chat)
    peer = peers.get_peer("cn-work", "42")
    assert (peer.chat, peers.peer_for_chat(chat)) == (None, None)


def test_forgetting_a_chat_leaves_the_others_alone():
    kept = peers.start_chat("cn-work", "42")
    doomed = peers.start_chat("cn-work", "42")
    peers.forget_chat(doomed)
    assert peers.peer_for_chat(kept).chat_id == "42"


# --- migration: a Peer recorded before Connections existed ---


def test_a_pre_connection_peer_is_adopted_by_its_platforms_connection():
    """An upgraded install's conversations continue in place: the Connection migrated
    for a platform takes over every Peer recorded against it."""
    peers._write([{"platform": "telegram", "chat_id": "42", "profile": "work"}])

    peers.adopt_connections({"telegram": "cn-1"})

    peer = peers.get_peer("cn-1", "42")
    assert (peer.profile, peer.platform) == ("work", "telegram")


def test_adoption_leaves_a_peer_that_already_has_a_connection_alone():
    peers.select_profile("cn-play", "42", "home", platform="telegram")

    peers.adopt_connections({"telegram": "cn-1"})

    assert peers.get_peer("cn-1", "42") is None
    assert peers.get_peer("cn-play", "42").profile == "home"
