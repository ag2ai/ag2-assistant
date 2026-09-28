"""The Peer registry — one conversation on the platform side, and what it remembers.

A Peer is keyed by (Connection, platform chat id) and holds the Profile that
conversation talks to. It is install-level state (ADR 0022): it spans Profiles by
construction, so it cannot live inside any one of them.
"""

import multiprocessing
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from assistant import peers
from assistant.peers import PeerStore
from tests.support.apps import make_paths


def test_an_unknown_conversation_has_no_peer_yet(paths):
    assert PeerStore(paths).get_peer("cn-work", "42") is None


def test_the_selected_profile_survives_a_restart(paths):
    """Nothing is cached in the process — a fresh read sees the same selection."""
    PeerStore(paths).select_profile("cn-work", "42", "work")
    assert PeerStore(paths).get_peer("cn-work", "42").profile == "work"


def test_two_conversations_hold_two_different_profiles(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work")
    PeerStore(paths).select_profile("cn-work", "99", "home")
    assert PeerStore(paths).get_peer("cn-work", "42").profile == "work"
    assert PeerStore(paths).get_peer("cn-work", "99").profile == "home"


def test_the_same_chat_id_on_two_connections_is_two_peers(paths):
    """A Telegram DM's chat id is the user's own id, the same across two bots — so the
    Connection is what keeps the two conversations apart."""
    PeerStore(paths).select_profile("cn-work", "42", "work", platform="telegram")
    PeerStore(paths).select_profile("cn-play", "42", "home", platform="telegram")
    assert PeerStore(paths).get_peer("cn-work", "42").profile == "work"
    assert PeerStore(paths).get_peer("cn-play", "42").profile == "home"


def test_two_connections_on_one_chat_id_start_two_chats(paths):
    first = PeerStore(paths).start_chat("cn-work", "42", platform="telegram")
    second = PeerStore(paths).start_chat("cn-play", "42", platform="telegram")
    assert first != second
    assert PeerStore(paths).get_peer("cn-work", "42").chat == first
    assert PeerStore(paths).get_peer("cn-play", "42").chat == second


def test_a_peer_remembers_which_surface_it_is(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work", surface="group")
    assert PeerStore(paths).get_peer("cn-work", "42").surface == "group"


def test_a_broken_registry_reads_as_no_peers(paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "peers.json").write_text("{ not json")
    assert PeerStore(paths).get_peer("cn-work", "42") is None


# --- the Chat a Peer is attached to ---


def test_a_peer_starts_attached_to_nothing(paths):
    """The Chat is materialised by the first message, not by the selection."""
    assert PeerStore(paths).select_profile("cn-work", "42", "work").chat is None


def test_a_started_chat_is_opaque_not_the_platform_address(paths):
    """A Chat id no longer doubles as an address, so a Peer can own more than one."""
    PeerStore(paths).select_profile("cn-work", "42", "work", platform="telegram")
    chat = PeerStore(paths).start_chat("cn-work", "42", platform="telegram")
    assert re.fullmatch(r"telegram-[0-9a-f]{8}", chat)


def test_starting_a_chat_attaches_the_peer_to_it(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work")
    chat = PeerStore(paths).start_chat("cn-work", "42")
    assert PeerStore(paths).get_peer("cn-work", "42").chat == chat


def test_two_started_chats_are_different_chats(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work")
    first = PeerStore(paths).start_chat("cn-work", "42")
    assert PeerStore(paths).start_chat("cn-work", "42") != first


def test_starting_a_chat_records_a_conversation_that_has_chosen_nothing(paths):
    """A Peer riding the Channel default has no selection of its own, and still owns
    the Chats it starts."""
    chat = PeerStore(paths).start_chat("cn-work", "42")
    peer = PeerStore(paths).get_peer("cn-work", "42")
    assert (peer.profile, peer.chat) == (None, chat)


def test_switching_profile_leaves_the_peer_attached_to_nothing(paths):
    """A Chat cannot cross Profiles, so the next message starts a fresh one."""
    PeerStore(paths).select_profile("cn-work", "42", "work")
    PeerStore(paths).start_chat("cn-work", "42")
    assert PeerStore(paths).select_profile("cn-work", "42", "home").chat is None


def test_reselecting_the_same_profile_keeps_the_attachment(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work")
    chat = PeerStore(paths).start_chat("cn-work", "42")
    assert PeerStore(paths).select_profile("cn-work", "42", "work").chat == chat


def test_attaching_to_an_existing_chat_moves_the_peer_without_starting_one(paths):
    """Attaching is pure navigation — nothing is minted, nothing is left behind."""
    PeerStore(paths).select_profile("cn-work", "42", "work")
    started = PeerStore(paths).start_chat("cn-work", "42")
    PeerStore(paths).attach("cn-work", "42", "web-abc123")
    peer = PeerStore(paths).get_peer("cn-work", "42")
    assert (peer.chat, peer.chats) == ("web-abc123", [started, "web-abc123"])


def test_attaching_back_to_a_chat_records_it_once(paths):
    started = PeerStore(paths).start_chat("cn-work", "42")
    PeerStore(paths).attach("cn-work", "42", "web-abc123")
    PeerStore(paths).attach("cn-work", "42", started)
    assert PeerStore(paths).get_peer("cn-work", "42").chats == [started, "web-abc123"]


# --- the model a Peer's next Chat is born on (ADR 0025) ---


def test_a_peer_holds_no_model_for_its_next_chat_by_default(paths):
    assert PeerStore(paths).select_profile("cn-work", "42", "work").pending_model is None


def test_the_held_model_survives_a_restart(paths):
    """It lives in peers.json with everything else the conversation remembers, so it
    is still there when the process that took it is gone."""
    PeerStore(paths).select_profile("cn-work", "42", "work")
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    assert PeerStore(paths).get_peer("cn-work", "42").pending_model == "c_deep"


def test_a_peer_recorded_before_held_models_existed_simply_holds_none(paths):
    """An install upgrading into this reads its old entries unchanged — the field is
    absent, which is the same as holding nothing."""
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "peers.json").write_text(
        '{"peers": [{"connection": "cn-work", "chat_id": "42", "profile": "work"}]}'
    )
    peer = PeerStore(paths).get_peer("cn-work", "42")
    assert (peer.profile, peer.pending_model) == ("work", None)


def test_taking_the_held_model_hands_it_over_once(paths):
    """It belongs to the Chat that message starts; a later Chat must not inherit it."""
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    assert PeerStore(paths).take_pending_model("cn-work", "42") == "c_deep"
    assert PeerStore(paths).take_pending_model("cn-work", "42") == ""
    assert PeerStore(paths).get_peer("cn-work", "42").pending_model is None


def test_taking_from_a_conversation_that_holds_nothing_is_quiet(paths):
    assert PeerStore(paths).take_pending_model("cn-work", "nobody") == ""


def test_an_empty_choice_drops_the_held_model(paths):
    """'Use default' arrives as no model at all."""
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    assert PeerStore(paths).set_pending_model("cn-work", "42", "").pending_model is None


def test_detaching_drops_the_held_model(paths):
    """`/new` starts over, and that includes the model chosen for a Chat that never was."""
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    PeerStore(paths).detach("cn-work", "42")
    assert PeerStore(paths).get_peer("cn-work", "42").pending_model is None


def test_attaching_drops_the_held_model(paths):
    """A held model belongs to a Peer Attached to nothing; holding one alongside a Chat
    is a state that cannot be spent, since only detaching gets back out of it."""
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    PeerStore(paths).attach("cn-work", "42", "web-abc123")
    assert PeerStore(paths).get_peer("cn-work", "42").pending_model is None


def test_starting_a_chat_spends_no_held_model_behind_the_routers_back(paths):
    """`start_chat` attaches too, so the router must take what it holds *before* asking
    for the Chat — this pins the half of that contract the store owns."""
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    PeerStore(paths).start_chat("cn-work", "42")
    assert PeerStore(paths).get_peer("cn-work", "42").pending_model is None


def test_switching_profile_drops_the_held_model(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work")
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    assert PeerStore(paths).select_profile("cn-work", "42", "home").pending_model is None


def test_reselecting_the_same_profile_keeps_the_held_model(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work")
    PeerStore(paths).set_pending_model("cn-work", "42", "c_deep")
    assert PeerStore(paths).select_profile("cn-work", "42", "work").pending_model == "c_deep"


# --- which Peer a Chat came from ---


def test_the_peer_a_chat_was_started_from_is_recoverable(paths):
    """That is how a task pushes its outcome back to the conversation it came from."""
    chat = PeerStore(paths).start_chat("cn-work", "42", platform="telegram")
    peer = PeerStore(paths).peer_for_chat(chat)
    assert (peer.connection, peer.chat_id) == ("cn-work", "42")


def test_a_chat_stays_with_its_peer_after_the_peer_has_moved_on(paths):
    chat = PeerStore(paths).start_chat("cn-work", "42")
    PeerStore(paths).start_chat("cn-work", "42")
    assert PeerStore(paths).peer_for_chat(chat).chat_id == "42"


def test_a_chat_nobody_started_belongs_to_no_peer(paths):
    PeerStore(paths).start_chat("cn-work", "42")
    assert PeerStore(paths).peer_for_chat("web-abc123") is None


def test_forgetting_a_chat_drops_it_from_its_peer(paths):
    """A deleted Chat is gone: neither attached nor owned."""
    chat = PeerStore(paths).start_chat("cn-work", "42")
    PeerStore(paths).forget_chat(chat)
    peer = PeerStore(paths).get_peer("cn-work", "42")
    assert (peer.chat, PeerStore(paths).peer_for_chat(chat)) == (None, None)


def test_forgetting_a_chat_leaves_the_others_alone(paths):
    kept = PeerStore(paths).start_chat("cn-work", "42")
    doomed = PeerStore(paths).start_chat("cn-work", "42")
    PeerStore(paths).forget_chat(doomed)
    assert PeerStore(paths).peer_for_chat(kept).chat_id == "42"


# --- migration: a Peer recorded before Connections existed ---


def test_a_pre_connection_peer_is_adopted_by_its_platforms_connection(paths):
    """An upgraded install's conversations continue in place: the Connection migrated
    for a platform takes over every Peer recorded against it."""
    PeerStore(paths)._write([{"platform": "telegram", "chat_id": "42", "profile": "work"}])

    PeerStore(paths).adopt_connections({"telegram": "cn-1"})

    peer = PeerStore(paths).get_peer("cn-1", "42")
    assert (peer.profile, peer.platform) == ("work", "telegram")


def test_adoption_leaves_a_peer_that_already_has_a_connection_alone(paths):
    PeerStore(paths).select_profile("cn-play", "42", "home", platform="telegram")

    PeerStore(paths).adopt_connections({"telegram": "cn-1"})

    assert PeerStore(paths).get_peer("cn-1", "42") is None
    assert PeerStore(paths).get_peer("cn-play", "42").profile == "home"


# --- migration: a Peer recorded before senders were ---


def _paired(*known: tuple[str, str]):
    return lambda connection, account: (connection, account) in known


def test_a_direct_peer_is_stamped_with_the_account_its_chat_id_names(paths):
    """A DM is named by the account id itself, so the pairing list recognises it."""
    PeerStore(paths).select_profile("cn-work", "42", "work", platform="telegram")

    assert PeerStore(paths).adopt_senders(_paired(("cn-work", "42"))) == 1
    assert PeerStore(paths).get_peer("cn-work", "42").sender == "42"


def test_a_peer_no_paired_account_names_is_left_without_a_sender(paths):
    """A group, or a platform whose chat ids are not account ids — it stays closed to a
    push until its next message stamps it."""
    PeerStore(paths).select_profile("cn-work", "-100", "work", platform="telegram")

    assert PeerStore(paths).adopt_senders(_paired(("cn-work", "42"))) == 0
    assert PeerStore(paths).get_peer("cn-work", "-100").sender == ""


def test_a_group_is_never_stamped_even_when_a_paired_account_names_it(paths):
    """A group whose chat id collides with a paired account id stays closed to a push."""
    PeerStore(paths).select_profile("cn-work", "42", "work", platform="telegram", surface="group")

    assert PeerStore(paths).adopt_senders(_paired(("cn-work", "42"))) == 0
    assert PeerStore(paths).get_peer("cn-work", "42").sender == ""


def test_a_peer_recorded_before_surfaces_were_is_never_stamped(paths):
    """No recorded surface is not a DM: it stamps on its next message instead."""
    PeerStore(paths)._write([{"connection": "cn-work", "chat_id": "42", "profile": "work"}])

    assert PeerStore(paths).adopt_senders(_paired(("cn-work", "42"))) == 0
    assert PeerStore(paths).get_peer("cn-work", "42").sender == ""


def test_stamping_senders_is_idempotent_and_never_overwrites_one(paths):
    PeerStore(paths).select_profile("cn-work", "42", "work", platform="telegram", sender="99")

    assert PeerStore(paths).adopt_senders(_paired(("cn-work", "42"))) == 0
    assert PeerStore(paths).get_peer("cn-work", "42").sender == "99"


# --- Cross-process safety -------------------------------------------------------
# peers.json is install-level, so the gateway and every stdio `ag2-assistant acp`
# child write it concurrently. These run real processes: an in-process thread pool
# would pass without the lock, because each read/write is one synchronous call.


def _append_peers(root: str, connection: str, count: int) -> None:
    """Record ``count`` fresh conversations, one at a time, in a child process."""
    store = PeerStore(make_paths(Path(root)))
    for n in range(count):
        store.select_profile(connection, str(n), "work", platform="telegram")


def _run(procs: list) -> None:
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert proc.exitcode == 0


def test_four_processes_appending_at_once_lose_no_peer(tmp_path):
    """The lost update this guards: two processes read the same entries, each appends
    its own, and the second write drops the first one's."""
    ctx = multiprocessing.get_context("spawn")
    _run([ctx.Process(target=_append_peers, args=(str(tmp_path), f"cn-{n}", 25)) for n in range(4)])

    assert len(PeerStore(make_paths(tmp_path)).list_peers()) == 100


def test_a_write_that_fails_leaves_the_previous_registry_intact(paths):
    """The swap is the write: until the rename lands, the old file is still the file.
    A plain write truncates first, so the same failure would leave nothing behind."""
    PeerStore(paths).select_profile("cn-work", "42", "work")
    registry = paths.root / "peers.json"
    before = registry.read_text()

    def _no_swap(*_args):
        raise OSError("no space left on device")

    with patch.object(peers, "os", wraps=os) as fake:
        fake.replace.side_effect = _no_swap
        with pytest.raises(OSError):
            PeerStore(paths).select_profile("cn-play", "99", "home")

    assert registry.read_text() == before
    assert not list(paths.root.glob(".peers-*")), "the temp file outlived the failure"
