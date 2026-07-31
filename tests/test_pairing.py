"""Paired accounts — who may reach a Connection at all (ADR 0021).

A numeric account id is an identity and is authoritative the moment it is entered.
A handle is only an invitation: it sits pending, pins to the numeric id of the first
account presenting it, and is matched by id ever after. Every one of these is a grant
to one Connection, so two Telegram bots have two rosters.
"""

from assistant import pairing

# Two Connections of the same platform, as an install with two Telegram bots has.
WORK = "cn_work"
HOME = "cn_home"

# --- entering an account by hand ---


def test_nobody_is_paired_on_a_fresh_install():
    assert pairing.list_accounts(WORK) == []
    assert pairing.is_paired(WORK, "42") is False


def test_a_numeric_id_is_authoritative_immediately():
    account = pairing.add_account(WORK, "42", "telegram")
    assert (account.account_id, account.pending) == ("42", False)
    assert pairing.is_paired(WORK, "42") is True


def test_a_handle_stays_pending_until_someone_bearing_it_speaks():
    account = pairing.add_account(WORK, "@nikita", "telegram")
    assert (account.handle, account.account_id, account.pending) == ("nikita", None, True)
    assert pairing.is_paired(WORK, "42") is False


def test_a_handle_is_entered_with_or_without_its_at_sign():
    assert pairing.add_account(WORK, "nikita", "telegram").handle == "nikita"


def test_a_handle_is_refused_where_messages_carry_none():
    """Slack sends only a user id, so a handle entry there could never be presented —
    it is refused rather than left pending forever."""
    try:
        pairing.add_account("cn_slack", "@nikita", "slack")
    except ValueError as exc:
        assert "numeric" in str(exc)
    else:
        raise AssertionError("a handle should not be accepted for slack")
    assert pairing.add_account("cn_slack", "42", "slack").account_id == "42"


def test_an_account_is_paired_on_one_connection_only():
    """The headline of a two-bot install: the work bot's roster is not the home bot's."""
    pairing.add_account(WORK, "42", "telegram")
    assert pairing.is_paired(HOME, "42") is False
    assert pairing.list_accounts(HOME) == []


def test_the_same_account_can_be_paired_to_both_connections_separately():
    pairing.add_account(WORK, "42", "telegram")
    pairing.add_account(HOME, "42", "telegram")
    assert pairing.is_paired(WORK, "42") is True
    assert pairing.is_paired(HOME, "42") is True


def test_entering_the_same_account_twice_leaves_one_entry():
    pairing.add_account(WORK, "42", "telegram")
    pairing.add_account(WORK, "42", "telegram")
    assert len(pairing.list_accounts(WORK)) == 1


# --- a pending handle pins to whoever presents it ---


def test_the_first_account_bearing_a_pending_handle_pins_it():
    pairing.add_account(WORK, "@nikita", "telegram")
    assert pairing.is_paired(WORK, "42", "nikita") is True

    pinned = pairing.list_accounts(WORK)[0]
    assert (pinned.account_id, pinned.handle, pinned.pending) == ("42", "nikita", False)


def test_an_invitation_pins_per_connection():
    """The same handle invited to both bots pins on each as it first speaks there."""
    pairing.add_account(WORK, "@nikita", "telegram")
    pairing.add_account(HOME, "@nikita", "telegram")
    assert pairing.is_paired(WORK, "42", "nikita") is True
    assert pairing.list_accounts(HOME)[0].pending is True


def test_a_handle_presented_with_its_at_sign_still_pins():
    pairing.add_account(WORK, "nikita", "telegram")
    assert pairing.is_paired(WORK, "42", "@nikita") is True


def test_a_handle_matches_regardless_of_case():
    pairing.add_account(WORK, "@Nikita", "telegram")
    assert pairing.is_paired(WORK, "42", "nikita") is True


def test_after_pinning_a_later_holder_of_the_handle_is_not_admitted():
    """A handle can be released and re-taken; the pairing does not follow it."""
    pairing.add_account(WORK, "@nikita", "telegram")
    pairing.is_paired(WORK, "42", "nikita")
    assert pairing.is_paired(WORK, "99", "nikita") is False


def test_after_pinning_the_original_account_keeps_access_under_a_new_handle():
    pairing.add_account(WORK, "@nikita", "telegram")
    pairing.is_paired(WORK, "42", "nikita")
    assert pairing.is_paired(WORK, "42", "someone-else-entirely") is True
    assert pairing.is_paired(WORK, "42", None) is True


# --- one-time codes ---


def test_a_code_pairs_the_account_that_presents_it():
    code = pairing.issue_code(WORK)
    assert pairing.redeem(WORK, code, "42") == pairing.PAIRED
    assert pairing.is_paired(WORK, "42") is True


def test_a_code_records_the_handle_of_the_account_that_used_it():
    code = pairing.issue_code(WORK)
    pairing.redeem(WORK, code, "42", "@nikita")
    assert pairing.list_accounts(WORK)[0].handle == "nikita"


def test_a_code_cannot_be_used_twice():
    code = pairing.issue_code(WORK)
    pairing.redeem(WORK, code, "42")
    assert pairing.redeem(WORK, code, "99") == pairing.UNKNOWN
    assert pairing.is_paired(WORK, "99") is False


def test_an_expired_code_is_refused_and_reported_as_expired():
    code = pairing.issue_code(WORK, ttl=-1)
    assert pairing.redeem(WORK, code, "42") == pairing.EXPIRED
    assert pairing.is_paired(WORK, "42") is False


def test_a_code_is_read_whatever_case_it_is_typed_in():
    code = pairing.issue_code(WORK)
    assert pairing.redeem(WORK, code.lower(), "42") == pairing.PAIRED


def test_a_code_issued_for_one_connection_does_nothing_on_another():
    """A code sent to the wrong bot of the same platform is simply unknown there."""
    code = pairing.issue_code(WORK)
    assert pairing.redeem(HOME, code, "42") == pairing.UNKNOWN
    assert pairing.is_paired(HOME, "42") is False
    assert pairing.redeem(WORK, code, "42") == pairing.PAIRED


def test_an_unissued_code_is_simply_unknown():
    assert pairing.redeem(WORK, "AAAA-1111", "42") == pairing.UNKNOWN


def test_a_code_is_shaped_so_ordinary_words_are_never_mistaken_for_one():
    code = pairing.issue_code(WORK)
    assert pairing.looks_like_code(code) is True
    assert pairing.looks_like_code("hello there") is False
    assert pairing.looks_like_code("/profile") is False


def test_the_live_code_is_visible_in_settings_until_it_is_used():
    code = pairing.issue_code(WORK)
    assert pairing.live_code(WORK).code == code
    pairing.redeem(WORK, code, "42")
    assert pairing.live_code(WORK) is None


def test_issuing_a_second_code_replaces_the_first():
    """Settings shows one code at a time, so the one on screen is the one that works."""
    first = pairing.issue_code(WORK)
    second = pairing.issue_code(WORK)
    assert pairing.redeem(WORK, first, "42") == pairing.UNKNOWN
    assert pairing.redeem(WORK, second, "42") == pairing.PAIRED


def test_issuing_a_code_leaves_another_connections_code_alone():
    home = pairing.issue_code(HOME)
    pairing.issue_code(WORK)
    assert pairing.live_code(HOME).code == home
    assert pairing.redeem(HOME, home, "42") == pairing.PAIRED


# --- revoking ---


def test_revoking_a_pinned_account_takes_effect_immediately():
    account = pairing.add_account(WORK, "42", "telegram")
    assert pairing.revoke(WORK, account.key) is True
    assert pairing.is_paired(WORK, "42") is False


def test_revoking_a_pending_handle_withdraws_the_invitation():
    account = pairing.add_account(WORK, "@nikita", "telegram")
    pairing.revoke(WORK, account.key)
    assert pairing.is_paired(WORK, "42", "nikita") is False


def test_revoking_something_that_is_not_there_reports_so():
    assert pairing.revoke(WORK, "42") is False


def test_revoking_one_account_leaves_the_others_alone():
    pairing.add_account(WORK, "42", "telegram")
    keep = pairing.add_account(WORK, "99", "telegram")
    pairing.revoke(WORK, "42")
    assert [a.key for a in pairing.list_accounts(WORK)] == [keep.key]


def test_revoking_affects_only_the_connection_it_was_revoked_from():
    pairing.add_account(WORK, "42", "telegram")
    pairing.add_account(HOME, "42", "telegram")
    pairing.revoke(WORK, "42")
    assert pairing.is_paired(HOME, "42") is True


# --- migration from an install that keyed pairing by platform ---


def test_migration_moves_a_platforms_roster_onto_its_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "_path", lambda: tmp_path / "pairing.json")
    (tmp_path / "pairing.json").write_text(
        '{"accounts": [{"platform": "telegram", "account_id": "42", "handle": null}],'
        ' "codes": [{"platform": "telegram", "code": "AAAA-1111", "expires_at": 1e12}]}'
    )
    pairing.adopt_connections({"telegram": WORK})
    assert pairing.is_paired(WORK, "42") is True
    assert pairing.live_code(WORK).code == "AAAA-1111"


def test_migration_leaves_an_unmigrated_platform_paired_to_nobody(tmp_path, monkeypatch):
    """A platform with no token has no Connection, so its old entries reach nothing."""
    monkeypatch.setattr(pairing, "_path", lambda: tmp_path / "pairing.json")
    (tmp_path / "pairing.json").write_text(
        '{"accounts": [{"platform": "discord", "account_id": "42"}], "codes": []}'
    )
    pairing.adopt_connections({"telegram": WORK})
    assert pairing.is_paired(WORK, "42") is False


def test_an_unadopted_entry_keeps_its_platform_for_a_later_migration(tmp_path, monkeypatch):
    """An adoption that skips a platform must not take its only key with it, or that
    roster can never be attributed to the Connection made for it later."""
    monkeypatch.setattr(pairing, "_path", lambda: tmp_path / "pairing.json")
    (tmp_path / "pairing.json").write_text(
        '{"accounts": [{"platform": "discord", "account_id": "42"}],'
        ' "codes": [{"platform": "discord", "code": "AAAA-1111", "expires_at": 1e12}]}'
    )
    pairing.adopt_connections({"telegram": WORK})
    pairing.adopt_connections({"discord": HOME})
    assert pairing.is_paired(HOME, "42") is True
    assert pairing.live_code(HOME).code == "AAAA-1111"


def test_an_adopted_entry_is_not_re_adopted(tmp_path, monkeypatch):
    """Adoption re-runs after an interrupted migration; a roster already moved stays put."""
    monkeypatch.setattr(pairing, "_path", lambda: tmp_path / "pairing.json")
    (tmp_path / "pairing.json").write_text(
        '{"accounts": [{"platform": "telegram", "account_id": "42"}], "codes": []}'
    )
    pairing.adopt_connections({"telegram": WORK})
    pairing.adopt_connections({"telegram": HOME})
    assert pairing.is_paired(WORK, "42") is True
    assert pairing.is_paired(HOME, "42") is False


# --- persistence ---


def test_the_paired_accounts_survive_a_restart():
    """Nothing is cached in the process — a fresh read sees the same accounts."""
    pairing.add_account(WORK, "42", "telegram")
    assert pairing.list_accounts(WORK)[0].account_id == "42"


def test_a_broken_registry_reads_as_nobody_paired(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "_path", lambda: tmp_path / "pairing.json")
    (tmp_path / "pairing.json").write_text("{ not json")
    assert pairing.list_accounts(WORK) == []
    assert pairing.is_paired(WORK, "42") is False
