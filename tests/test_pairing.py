"""Paired accounts — who may reach a Connection at all (ADR 0021).

A numeric account id is an identity and is authoritative the moment it is entered.
A handle is only an invitation: it sits pending, pins to the numeric id of the first
account presenting it, and is matched by id ever after. Every one of these is a grant
to one Connection, so two Telegram bots have two rosters.
"""

from assistant import pairing as pairing_mod
from assistant.pairing import PairingStore

# Two Connections of the same platform, as an install with two Telegram bots has.
WORK = "cn_work"
HOME = "cn_home"

# --- entering an account by hand ---


def test_nobody_is_paired_on_a_fresh_install(paths):
    assert PairingStore(paths).list_accounts(WORK) == []
    assert PairingStore(paths).is_paired(WORK, "42") is False


def test_a_numeric_id_is_authoritative_immediately(paths):
    account = PairingStore(paths).add_account(WORK, "42", "telegram")
    assert (account.account_id, account.pending) == ("42", False)
    assert PairingStore(paths).is_paired(WORK, "42") is True


def test_a_handle_stays_pending_until_someone_bearing_it_speaks(paths):
    account = PairingStore(paths).add_account(WORK, "@nikita", "telegram")
    assert (account.handle, account.account_id, account.pending) == ("nikita", None, True)
    assert PairingStore(paths).is_paired(WORK, "42") is False


def test_a_handle_is_entered_with_or_without_its_at_sign(paths):
    assert PairingStore(paths).add_account(WORK, "nikita", "telegram").handle == "nikita"


def test_a_handle_is_refused_where_messages_carry_none(paths):
    """Slack sends only a user id, so a handle entry there could never be presented —
    it is refused rather than left pending forever."""
    try:
        PairingStore(paths).add_account("cn_slack", "@nikita", "slack")
    except ValueError as exc:
        assert "numeric" in str(exc)
    else:
        raise AssertionError("a handle should not be accepted for slack")
    assert PairingStore(paths).add_account("cn_slack", "42", "slack").account_id == "42"


def test_an_account_is_paired_on_one_connection_only(paths):
    """The headline of a two-bot install: the work bot's roster is not the home bot's."""
    PairingStore(paths).add_account(WORK, "42", "telegram")
    assert PairingStore(paths).is_paired(HOME, "42") is False
    assert PairingStore(paths).list_accounts(HOME) == []


def test_the_same_account_can_be_paired_to_both_connections_separately(paths):
    PairingStore(paths).add_account(WORK, "42", "telegram")
    PairingStore(paths).add_account(HOME, "42", "telegram")
    assert PairingStore(paths).is_paired(WORK, "42") is True
    assert PairingStore(paths).is_paired(HOME, "42") is True


def test_entering_the_same_account_twice_leaves_one_entry(paths):
    PairingStore(paths).add_account(WORK, "42", "telegram")
    PairingStore(paths).add_account(WORK, "42", "telegram")
    assert len(PairingStore(paths).list_accounts(WORK)) == 1


# --- a pending handle pins to whoever presents it ---


def test_the_first_account_bearing_a_pending_handle_pins_it(paths):
    PairingStore(paths).add_account(WORK, "@nikita", "telegram")
    assert PairingStore(paths).is_paired(WORK, "42", "nikita") is True

    pinned = PairingStore(paths).list_accounts(WORK)[0]
    assert (pinned.account_id, pinned.handle, pinned.pending) == ("42", "nikita", False)


def test_an_invitation_pins_per_connection(paths):
    """The same handle invited to both bots pins on each as it first speaks there."""
    PairingStore(paths).add_account(WORK, "@nikita", "telegram")
    PairingStore(paths).add_account(HOME, "@nikita", "telegram")
    assert PairingStore(paths).is_paired(WORK, "42", "nikita") is True
    assert PairingStore(paths).list_accounts(HOME)[0].pending is True


def test_a_handle_presented_with_its_at_sign_still_pins(paths):
    PairingStore(paths).add_account(WORK, "nikita", "telegram")
    assert PairingStore(paths).is_paired(WORK, "42", "@nikita") is True


def test_a_handle_matches_regardless_of_case(paths):
    PairingStore(paths).add_account(WORK, "@Nikita", "telegram")
    assert PairingStore(paths).is_paired(WORK, "42", "nikita") is True


def test_after_pinning_a_later_holder_of_the_handle_is_not_admitted(paths):
    """A handle can be released and re-taken; the pairing does not follow it."""
    PairingStore(paths).add_account(WORK, "@nikita", "telegram")
    PairingStore(paths).is_paired(WORK, "42", "nikita")
    assert PairingStore(paths).is_paired(WORK, "99", "nikita") is False


def test_after_pinning_the_original_account_keeps_access_under_a_new_handle(paths):
    PairingStore(paths).add_account(WORK, "@nikita", "telegram")
    PairingStore(paths).is_paired(WORK, "42", "nikita")
    assert PairingStore(paths).is_paired(WORK, "42", "someone-else-entirely") is True
    assert PairingStore(paths).is_paired(WORK, "42", None) is True


# --- one-time codes ---


def test_a_code_pairs_the_account_that_presents_it(paths):
    code = PairingStore(paths).issue_code(WORK)
    assert PairingStore(paths).redeem(WORK, code, "42") == pairing_mod.PAIRED
    assert PairingStore(paths).is_paired(WORK, "42") is True


def test_a_code_records_the_handle_of_the_account_that_used_it(paths):
    code = PairingStore(paths).issue_code(WORK)
    PairingStore(paths).redeem(WORK, code, "42", "@nikita")
    assert PairingStore(paths).list_accounts(WORK)[0].handle == "nikita"


def test_a_code_cannot_be_used_twice(paths):
    code = PairingStore(paths).issue_code(WORK)
    PairingStore(paths).redeem(WORK, code, "42")
    assert PairingStore(paths).redeem(WORK, code, "99") == pairing_mod.UNKNOWN
    assert PairingStore(paths).is_paired(WORK, "99") is False


def test_an_expired_code_is_refused_and_reported_as_expired(paths):
    code = PairingStore(paths).issue_code(WORK, ttl=-1)
    assert PairingStore(paths).redeem(WORK, code, "42") == pairing_mod.EXPIRED
    assert PairingStore(paths).is_paired(WORK, "42") is False


def test_a_code_is_read_whatever_case_it_is_typed_in(paths):
    code = PairingStore(paths).issue_code(WORK)
    assert PairingStore(paths).redeem(WORK, code.lower(), "42") == pairing_mod.PAIRED


def test_a_code_issued_for_one_connection_does_nothing_on_another(paths):
    """A code sent to the wrong bot of the same platform is simply unknown there."""
    code = PairingStore(paths).issue_code(WORK)
    assert PairingStore(paths).redeem(HOME, code, "42") == pairing_mod.UNKNOWN
    assert PairingStore(paths).is_paired(HOME, "42") is False
    assert PairingStore(paths).redeem(WORK, code, "42") == pairing_mod.PAIRED


def test_an_unissued_code_is_simply_unknown(paths):
    assert PairingStore(paths).redeem(WORK, "AAAA-1111", "42") == pairing_mod.UNKNOWN


def test_a_code_is_shaped_so_ordinary_words_are_never_mistaken_for_one(paths):
    code = PairingStore(paths).issue_code(WORK)
    assert pairing_mod.looks_like_code(code) is True
    assert pairing_mod.looks_like_code("hello there") is False
    assert pairing_mod.looks_like_code("/profile") is False


def test_the_live_code_is_visible_in_settings_until_it_is_used(paths):
    code = PairingStore(paths).issue_code(WORK)
    assert PairingStore(paths).live_code(WORK).code == code
    PairingStore(paths).redeem(WORK, code, "42")
    assert PairingStore(paths).live_code(WORK) is None


def test_issuing_a_second_code_replaces_the_first(paths):
    """Settings shows one code at a time, so the one on screen is the one that works."""
    first = PairingStore(paths).issue_code(WORK)
    second = PairingStore(paths).issue_code(WORK)
    assert PairingStore(paths).redeem(WORK, first, "42") == pairing_mod.UNKNOWN
    assert PairingStore(paths).redeem(WORK, second, "42") == pairing_mod.PAIRED


def test_issuing_a_code_leaves_another_connections_code_alone(paths):
    home = PairingStore(paths).issue_code(HOME)
    PairingStore(paths).issue_code(WORK)
    assert PairingStore(paths).live_code(HOME).code == home
    assert PairingStore(paths).redeem(HOME, home, "42") == pairing_mod.PAIRED


# --- revoking ---


def test_revoking_a_pinned_account_takes_effect_immediately(paths):
    account = PairingStore(paths).add_account(WORK, "42", "telegram")
    assert PairingStore(paths).revoke(WORK, account.key) is True
    assert PairingStore(paths).is_paired(WORK, "42") is False


def test_revoking_a_pending_handle_withdraws_the_invitation(paths):
    account = PairingStore(paths).add_account(WORK, "@nikita", "telegram")
    PairingStore(paths).revoke(WORK, account.key)
    assert PairingStore(paths).is_paired(WORK, "42", "nikita") is False


def test_revoking_something_that_is_not_there_reports_so(paths):
    assert PairingStore(paths).revoke(WORK, "42") is False


def test_revoking_one_account_leaves_the_others_alone(paths):
    PairingStore(paths).add_account(WORK, "42", "telegram")
    keep = PairingStore(paths).add_account(WORK, "99", "telegram")
    PairingStore(paths).revoke(WORK, "42")
    assert [a.key for a in PairingStore(paths).list_accounts(WORK)] == [keep.key]


def test_revoking_affects_only_the_connection_it_was_revoked_from(paths):
    PairingStore(paths).add_account(WORK, "42", "telegram")
    PairingStore(paths).add_account(HOME, "42", "telegram")
    PairingStore(paths).revoke(WORK, "42")
    assert PairingStore(paths).is_paired(HOME, "42") is True


# --- migration from an install that keyed pairing by platform ---


def test_migration_moves_a_platforms_roster_onto_its_connection(paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "pairing.json").write_text(
        '{"accounts": [{"platform": "telegram", "account_id": "42", "handle": null}],'
        ' "codes": [{"platform": "telegram", "code": "AAAA-1111", "expires_at": 1e12}]}'
    )
    PairingStore(paths).adopt_connections({"telegram": WORK})
    assert PairingStore(paths).is_paired(WORK, "42") is True
    assert PairingStore(paths).live_code(WORK).code == "AAAA-1111"


def test_migration_leaves_an_unmigrated_platform_paired_to_nobody(paths):
    """A platform with no token has no Connection, so its old entries reach nothing."""
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "pairing.json").write_text(
        '{"accounts": [{"platform": "discord", "account_id": "42"}], "codes": []}'
    )
    PairingStore(paths).adopt_connections({"telegram": WORK})
    assert PairingStore(paths).is_paired(WORK, "42") is False


def test_an_unadopted_entry_keeps_its_platform_for_a_later_migration(paths):
    """An adoption that skips a platform must not take its only key with it, or that
    roster can never be attributed to the Connection made for it later."""
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "pairing.json").write_text(
        '{"accounts": [{"platform": "discord", "account_id": "42"}],'
        ' "codes": [{"platform": "discord", "code": "AAAA-1111", "expires_at": 1e12}]}'
    )
    PairingStore(paths).adopt_connections({"telegram": WORK})
    PairingStore(paths).adopt_connections({"discord": HOME})
    assert PairingStore(paths).is_paired(HOME, "42") is True
    assert PairingStore(paths).live_code(HOME).code == "AAAA-1111"


def test_an_adopted_entry_is_not_re_adopted(paths):
    """Adoption re-runs after an interrupted migration; a roster already moved stays put."""
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "pairing.json").write_text(
        '{"accounts": [{"platform": "telegram", "account_id": "42"}], "codes": []}'
    )
    PairingStore(paths).adopt_connections({"telegram": WORK})
    PairingStore(paths).adopt_connections({"telegram": HOME})
    assert PairingStore(paths).is_paired(WORK, "42") is True
    assert PairingStore(paths).is_paired(HOME, "42") is False


# --- persistence ---


def test_the_paired_accounts_survive_a_restart(paths):
    """Nothing is cached in the process — a fresh read sees the same accounts."""
    PairingStore(paths).add_account(WORK, "42", "telegram")
    assert PairingStore(paths).list_accounts(WORK)[0].account_id == "42"


def test_a_broken_registry_reads_as_nobody_paired(paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "pairing.json").write_text("{ not json")
    assert PairingStore(paths).list_accounts(WORK) == []
    assert PairingStore(paths).is_paired(WORK, "42") is False
