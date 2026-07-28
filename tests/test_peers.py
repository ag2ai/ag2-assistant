"""The Peer registry — one conversation on the platform side, and what it remembers.

A Peer is keyed by (platform, platform chat id) and holds the Profile that
conversation talks to. It is install-level state (ADR 0019): it spans Profiles by
construction, so it cannot live inside any one of them.
"""

from assistant import peers


def test_an_unknown_conversation_has_no_peer_yet():
    assert peers.get_peer("telegram", "42") is None


def test_the_selected_profile_survives_a_restart():
    """Nothing is cached in the process — a fresh read sees the same selection."""
    peers.select_profile("telegram", "42", "work")
    assert peers.get_peer("telegram", "42").profile == "work"


def test_two_conversations_hold_two_different_profiles():
    peers.select_profile("telegram", "42", "work")
    peers.select_profile("telegram", "99", "home")
    assert peers.get_peer("telegram", "42").profile == "work"
    assert peers.get_peer("telegram", "99").profile == "home"


def test_the_same_chat_id_on_two_platforms_is_two_peers():
    peers.select_profile("telegram", "42", "work")
    peers.select_profile("discord", "42", "home")
    assert peers.get_peer("telegram", "42").profile == "work"
    assert peers.get_peer("discord", "42").profile == "home"


def test_a_peer_remembers_which_surface_it_is():
    peers.select_profile("telegram", "42", "work", surface="group")
    assert peers.get_peer("telegram", "42").surface == "group"


def test_a_broken_registry_reads_as_no_peers(tmp_path, monkeypatch):
    monkeypatch.setattr(peers, "_path", lambda: tmp_path / "peers.json")
    (tmp_path / "peers.json").write_text("{ not json")
    assert peers.get_peer("telegram", "42") is None


# --- the Chat a Peer's turns run on ---


def test_the_first_selection_keeps_the_conversations_own_chat():
    """Nothing has been switched yet, so the Peer stays in the Chat it started in."""
    peer = peers.select_profile("telegram", "42", "work")
    assert peer.chat() == "telegram:42"


def test_switching_profile_moves_the_peer_to_a_fresh_chat():
    peers.select_profile("telegram", "42", "work")
    left = peers.get_peer("telegram", "42").chat()
    moved = peers.select_profile("telegram", "42", "home").chat()
    assert moved != left


def test_switching_back_does_not_return_to_the_chat_that_was_left():
    """A Chat cannot cross Profiles, and returning to a Profile is /resume's job —
    every switch mints a new Chat, even one back to where it came from."""
    first = peers.select_profile("telegram", "42", "work").chat()
    peers.select_profile("telegram", "42", "home")
    third = peers.select_profile("telegram", "42", "work").chat()
    assert third != first


def test_reselecting_the_same_profile_is_not_a_switch():
    first = peers.select_profile("telegram", "42", "work").chat()
    assert peers.select_profile("telegram", "42", "work").chat() == first


def test_a_peers_chat_stays_inside_its_own_conversation():
    """Whatever the Chat id becomes, the platform address it is derived from is
    still recoverable — that is how a task pushes its outcome back."""
    peers.select_profile("telegram", "42", "work")
    peers.select_profile("telegram", "42", "home")
    assert peers.get_peer("telegram", "42").chat().startswith("telegram:42")
