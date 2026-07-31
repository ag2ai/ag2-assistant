"""Paired accounts persisted to ``<root>/pairing.json`` — who may reach a Connection.

An account not paired to a Connection is served nothing at all by it (ADR 0021), and
the grant is to that one Connection: being paired to the work Telegram bot gives no
access to the personal one. Identity is the platform's numeric id; a handle is only an
invitation, so a handle entered in Settings sits *pending*, pins to the numeric id of
the first account presenting it, and is matched by id ever after. The other way to pair
is a one-time **code**, minted per Connection and sent to that bot.

Read/write style mirrors ``peers.py``: a small read-modify-write over a JSON file,
tolerant of a missing/malformed file (treated as nobody paired — the safe direction).
"""

import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from assistant.config import data_dir

# Outcomes of presenting a code.
PAIRED = "paired"
EXPIRED = "expired"
UNKNOWN = "unknown"

# A code is two groups of four, from an alphabet with no look-alike characters, so it
# survives being read off one screen and typed into another. The dash is what keeps
# ordinary prose from ever being mistaken for a code (see ``looks_like_code``).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_RE = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}")

# Long enough to walk from the browser to the phone, short enough that a code left on
# a screen goes stale.
CODE_TTL = 15 * 60.0

# Platforms whose inbound messages carry the sender's handle. Elsewhere an invitation
# by handle could never be presented, so it is refused rather than left pending forever.
HANDLE_PLATFORMS = ("telegram", "discord")


@dataclass(frozen=True)
class PairedAccount:
    """One account allowed to reach a Connection.

    Pinned once ``account_id`` is known; *pending* while it is only a handle."""

    connection: str
    account_id: str | None = None
    handle: str | None = None

    @property
    def pending(self) -> bool:
        """Whether this is still an invitation waiting for someone to present it."""
        return self.account_id is None

    @property
    def key(self) -> str:
        """Stable handle for revoking this entry — its id, or the handle it awaits."""
        return self.account_id if self.account_id is not None else f"@{self.handle}"


@dataclass(frozen=True)
class PairingCode:
    """A one-time code and the moment it stops working."""

    connection: str
    code: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


def normalise_handle(value: str) -> str:
    """A handle without its ``@`` and folded for comparison — the stored form."""
    return value.strip().lstrip("@").casefold()


def looks_like_code(text: str) -> bool:
    """Whether this text could be a pairing code. Deliberately narrow: it decides
    whether an *unpaired* account is answered at all, and everything else is silence."""
    return _CODE_RE.fullmatch(text.strip().upper()) is not None


def _path() -> Path:
    return data_dir() / "pairing.json"


def _load() -> dict:
    """The stored registry (empty if the file is absent or malformed)."""
    try:
        data = json.loads(_path().read_text())
    except Exception:
        return {"accounts": [], "codes": []}
    if not isinstance(data, dict):
        return {"accounts": [], "codes": []}
    accounts = data.get("accounts")
    codes = data.get("codes")
    return {
        "accounts": accounts if isinstance(accounts, list) else [],
        "codes": codes if isinstance(codes, list) else [],
    }


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def _account(entry: dict) -> PairedAccount:
    return PairedAccount(
        connection=entry.get("connection", ""),
        account_id=entry.get("account_id"),
        handle=entry.get("handle"),
    )


def _entry(account: PairedAccount) -> dict:
    return {
        "connection": account.connection,
        "account_id": account.account_id,
        "handle": account.handle,
    }


def _code(entry: dict) -> PairingCode:
    return PairingCode(
        connection=entry.get("connection", ""),
        code=entry["code"],
        expires_at=float(entry.get("expires_at", 0.0)),
    )


def list_accounts(connection: str) -> list[PairedAccount]:
    """Every account allowed to reach ``connection``, in the order they were added."""
    return [_account(e) for e in _load()["accounts"] if e.get("connection") == connection]


def add_account(connection: str, value: str, platform: str) -> PairedAccount:
    """Allow an account on one Connection, entered as a numeric id or as a handle.

    A numeric id is authoritative at once; a handle is stored pending. ``platform``
    says whether a handle can ever be presented. Re-entering something already on the
    list returns the existing entry rather than doubling it."""
    value = value.strip()
    if not value:
        raise ValueError("enter a numeric account id or a handle")

    if value.isdigit():
        account = PairedAccount(connection=connection, account_id=value)
    else:
        handle = normalise_handle(value)
        if not handle:
            raise ValueError("enter a numeric account id or a handle")
        if platform not in HANDLE_PLATFORMS:
            raise ValueError(f"{platform} messages carry no handle — enter a numeric account id")
        account = PairedAccount(connection=connection, handle=handle)

    data = _load()
    for entry in data["accounts"]:
        existing = _account(entry)
        if existing.connection == connection and existing.key == account.key:
            return existing
    data["accounts"].append(_entry(account))
    _write(data)
    return account


def revoke(connection: str, key: str) -> bool:
    """Withdraw one entry by its ``key``. True when something was removed."""
    data = _load()
    kept = [
        e
        for e in data["accounts"]
        if not (e.get("connection") == connection and _account(e).key == key)
    ]
    if len(kept) == len(data["accounts"]):
        return False
    data["accounts"] = kept
    _write(data)
    return True


def is_paired(connection: str, account_id: str, handle: str | None = None) -> bool:
    """Whether this account may be served by this Connection — pinning a pending
    handle it presents.

    Matching is by id for every entry that has one, so a handle changing hands after
    it pinned admits nobody new and locks nobody out."""
    data = _load()
    pending: int | None = None
    wanted = normalise_handle(handle) if handle else None

    for index, entry in enumerate(data["accounts"]):
        account = _account(entry)
        if account.connection != connection:
            continue
        if account.account_id == account_id:
            return True
        if pending is None and account.pending and wanted and account.handle == wanted:
            pending = index

    if pending is None:
        return False
    # First arrival on an invitation: the handle stops being how we recognise them.
    data["accounts"][pending] = _entry(
        PairedAccount(connection=connection, account_id=account_id, handle=wanted)
    )
    _write(data)
    return True


def issue_code(connection: str, *, ttl: float = CODE_TTL) -> str:
    """Mint the one live pairing code for ``connection``, replacing any earlier one of
    its own so the code on screen in Settings is always the code that works."""
    code = "-".join("".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(2))
    data = _load()
    data["codes"] = [e for e in data["codes"] if e.get("connection") != connection]
    data["codes"].append({"connection": connection, "code": code, "expires_at": time.time() + ttl})
    _write(data)
    return code


def live_code(connection: str) -> PairingCode | None:
    """The code Settings should show — unused and not yet expired, else None."""
    for entry in _load()["codes"]:
        if entry.get("connection") != connection:
            continue
        code = _code(entry)
        return None if code.expired else code
    return None


def redeem(connection: str, code: str, account_id: str, handle: str | None = None) -> str:
    """Present ``code`` to a Connection: ``PAIRED``, ``EXPIRED`` or ``UNKNOWN``.

    A spent code is removed, so it cannot pair a second account. An expired one is
    removed too — it is reported once, to the person who was clearly sent it."""
    wanted = code.strip().upper()
    data = _load()
    match = next(
        (e for e in data["codes"] if e.get("connection") == connection and e.get("code") == wanted),
        None,
    )
    if match is None:
        return UNKNOWN

    data["codes"].remove(match)
    if _code(match).expired:
        _write(data)
        return EXPIRED

    account = PairedAccount(
        connection=connection,
        account_id=account_id,
        handle=normalise_handle(handle) if handle else None,
    )
    if not any(
        e.get("connection") == connection and _account(e).account_id == account_id
        for e in data["accounts"]
    ):
        data["accounts"].append(_entry(account))
    _write(data)
    return PAIRED


def adopt_connections(by_platform: dict[str, str]) -> None:
    """Move each platform's paired accounts and live code onto the Connection migrated
    for it, so nobody who could reach the assistant before loses access."""
    data = _load()
    for entry in data["accounts"] + data["codes"]:
        connection = by_platform.get(entry.pop("platform", None))
        if connection and not entry.get("connection"):
            entry["connection"] = connection
    if data["accounts"] or data["codes"]:
        _write(data)
