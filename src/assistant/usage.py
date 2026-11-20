"""Token-usage ledger + cost estimation for the Cost & Activity HUD.

Tokens are tracked **exactly** — summed from AG2 ``UsageEvent``s emitted on the
stream each turn. Cost is a **best-effort estimate** from a per-model price table
(USD per 1M tokens); the table is editable via ``~/.ag2assistant/pricing.json`` and
unknown models report tokens only (no cost). ``prompt_tokens`` includes cache reads
and writes (AG2 ≥ 1.3.2, every provider), so a model with cache rates has its cached
part priced at those rates and only the rest at the input rate. The daily ledger persists to
``~/.ag2assistant/usage.json`` so totals survive restarts.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from ag2.events import Usage


class Price(NamedTuple):
    """USD per 1M tokens. A cache rate left ``None`` prices those tokens as input."""

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    """Writes with the default retention (Anthropic: 5 minutes)."""
    cache_write_extended: float | None = None
    """Writes with extended retention (Anthropic: 1 hour)."""


# Approximate published list prices, USD per 1M tokens, as (input, output). Matched
# by SUBSTRING against the model id (first match wins). These are ESTIMATES for the
# HUD, not billing truth — override any of them in ~/.ag2assistant/pricing.json, e.g.
#   {"gemini-3.6-flash": [0.30, 2.50], "gpt-5": {"input": 1.25, "output": 10.0}}
# A dict entry may also set "cache_read", "cache_write" and "cache_write_extended".
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.6-flash": (1.50, 7.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-3-pro": (2.00, 12.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gpt-5": (1.25, 10.00),
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-4o": (2.50, 10.00),
    "claude-opus": (5.00, 25.00),
    "claude-sonnet": (2.00, 10.00),
    "claude-haiku": (1.00, 5.00),
}


# Anthropic prices cache traffic as multiples of the input rate: reads 0.1x, writes
# 1.25x (5-minute retention) or 2x (1-hour retention).
_ANTHROPIC_CACHE = (0.1, 1.25, 2.0)


def _optional(value: Any) -> float | None:
    return None if value is None else float(value)


def _with_default_cache_rates(key: str, price: Price) -> Price:
    """Fill a Claude entry's unset cache rates from Anthropic's multipliers."""
    if "claude" not in key:
        return price
    read, write, extended = (price.input * m for m in _ANTHROPIC_CACHE)
    return price._replace(
        cache_read=read if price.cache_read is None else price.cache_read,
        cache_write=write if price.cache_write is None else price.cache_write,
        cache_write_extended=(
            extended if price.cache_write_extended is None else price.cache_write_extended
        ),
    )


def load_pricing(path: Path) -> dict[str, Price]:
    """Default table merged with any user overrides from ``path`` (an absent or
    malformed file leaves the built-in table untouched)."""
    table = {key: Price(*val) for key, val in _DEFAULT_PRICING.items()}
    try:
        raw = json.loads(Path(path).read_text())
        for key, val in raw.items():
            if isinstance(val, (list, tuple)) and len(val) == 2:
                table[key.lower()] = Price(float(val[0]), float(val[1]))
            elif isinstance(val, dict):
                table[key.lower()] = Price(
                    float(val.get("input", 0)),
                    float(val.get("output", 0)),
                    _optional(val.get("cache_read")),
                    _optional(val.get("cache_write")),
                    _optional(val.get("cache_write_extended")),
                )
    except Exception:
        pass
    return {key: _with_default_cache_rates(key, price) for key, price in table.items()}


def estimate_cost(
    model: str,
    prompt_tokens: float,
    completion_tokens: float,
    *,
    pricing: Mapping[str, Price],
    cache_read: float = 0,
    cache_write: float = 0,
    cache_write_extended: float = 0,
) -> float | None:
    """Estimated USD cost for a turn, or None if the model has no known price.

    ``cache_read`` and ``cache_write`` are parts of ``prompt_tokens``, and
    ``cache_write_extended`` is the part of ``cache_write`` with extended retention.
    """
    m = (model or "").lower()
    for key, price in pricing.items():
        if key in m:
            inp = price.input
            write_rate = inp if price.cache_write is None else price.cache_write
            extended_rate = (
                write_rate if price.cache_write_extended is None else price.cache_write_extended
            )
            read_rate = inp if price.cache_read is None else price.cache_read
            uncached = max((prompt_tokens or 0) - cache_read - cache_write, 0)
            dollars = (
                uncached * inp
                + cache_read * read_rate
                + max(cache_write - cache_write_extended, 0) * write_rate
                + cache_write_extended * extended_rate
                + (completion_tokens or 0) * price.output
            )
            return dollars / 1e6
    return None


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _blank() -> dict:
    return {
        "prompt": 0.0,
        "completion": 0.0,
        "total": 0.0,
        "cost": 0.0,
        "priced": False,
        "by_model": {},
    }


class UsageLedger:
    """Daily token + estimated-cost totals, persisted to disk. Thread-safe."""

    def __init__(self, path: Path, *, pricing_path: Path):
        self._path = Path(path)
        # Where a user's price overrides live (install-wide); re-read per record so an
        # edit applies without a restart, exactly as before.
        self._pricing_path = Path(pricing_path)
        self._lock = threading.Lock()
        self._days: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._days = json.loads(self._path.read_text())
        except Exception:
            self._days = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._days))
        except Exception:
            pass

    def record(
        self,
        model: str,
        prompt_tokens: float = 0,
        completion_tokens: float = 0,
        total_tokens: float | None = None,
        *,
        day: str | None = None,
        cache_read: float = 0,
        cache_write: float = 0,
        cache_write_extended: float = 0,
    ) -> None:
        """Add one turn's usage to the given day (default: today). The cache counts
        are parts of ``prompt_tokens`` (see ``estimate_cost``)."""
        prompt = prompt_tokens or 0
        completion = completion_tokens or 0
        total = total_tokens or (prompt + completion)
        cost = estimate_cost(
            model,
            prompt,
            completion,
            pricing=load_pricing(self._pricing_path),
            cache_read=cache_read,
            cache_write=cache_write,
            cache_write_extended=cache_write_extended,
        )
        day = day or _today()
        with self._lock:
            d = self._days.setdefault(day, _blank())
            d["prompt"] += prompt
            d["completion"] += completion
            d["total"] += total
            if cost is not None:
                d["cost"] += cost
                d["priced"] = True
            bm = d["by_model"].setdefault(
                model or "unknown",
                {"prompt": 0.0, "completion": 0.0, "total": 0.0, "cost": 0.0, "priced": False},
            )
            bm["prompt"] += prompt
            bm["completion"] += completion
            bm["total"] += total
            if cost is not None:
                bm["cost"] += cost
                bm["priced"] = True
            self._save()

    def record_usage(self, model: str, usage: Usage) -> None:
        """Add one AG2 ``Usage`` (one call's, or a turn's sum) with its cache breakdown."""
        self.record(
            model,
            usage.prompt_tokens or 0,
            usage.completion_tokens or 0,
            usage.total_tokens or None,
            cache_read=usage.cache_read_input_tokens or 0,
            cache_write=usage.cache_creation_input_tokens or 0,
            cache_write_extended=usage.cache_creation_extended_input_tokens or 0,
        )

    def today(self, day: str | None = None) -> dict:
        """Snapshot of one day's totals (default: today), plus the date."""
        day = day or _today()
        with self._lock:
            d = self._days.get(day) or _blank()
            return {
                "date": day,
                **{k: (dict(v) if isinstance(v, dict) else v) for k, v in d.items()},
            }
