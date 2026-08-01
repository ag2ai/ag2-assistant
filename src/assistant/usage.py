"""Token-usage ledger + cost estimation for the Cost & Activity HUD.

Tokens are tracked **exactly** — summed from AG2 ``UsageEvent``s emitted on the
stream each turn. Cost is a **best-effort estimate** from a per-model price table
(USD per 1M tokens); the table is editable via ``~/.ag2assistant/pricing.json`` and
unknown models report tokens only (no cost). The daily ledger persists to
``~/.ag2assistant/usage.json`` so totals survive restarts.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

# Approximate published list prices, USD per 1M tokens, as (input, output). Matched
# by SUBSTRING against the model id (first match wins). These are ESTIMATES for the
# HUD, not billing truth — override any of them in ~/.ag2assistant/pricing.json, e.g.
#   {"gemini-3.6-flash": [0.30, 2.50], "gpt-5": {"input": 1.25, "output": 10.0}}
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


def load_pricing(path: Path) -> dict[str, tuple[float, float]]:
    """Default table merged with any user overrides from ``path`` (an absent or
    malformed file leaves the built-in table untouched)."""
    table = dict(_DEFAULT_PRICING)
    try:
        raw = json.loads(Path(path).read_text())
        for key, val in raw.items():
            if isinstance(val, (list, tuple)) and len(val) == 2:
                table[key.lower()] = (float(val[0]), float(val[1]))
            elif isinstance(val, dict):
                table[key.lower()] = (float(val.get("input", 0)), float(val.get("output", 0)))
    except Exception:
        pass
    return table


def estimate_cost(
    model: str,
    prompt_tokens: float,
    completion_tokens: float,
    *,
    pricing: Mapping[str, tuple[float, float]],
) -> float | None:
    """Estimated USD cost for a turn, or None if the model has no known price."""
    m = (model or "").lower()
    for key, (inp, out) in pricing.items():
        if key in m:
            return (prompt_tokens or 0) / 1e6 * inp + (completion_tokens or 0) / 1e6 * out
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
    ) -> None:
        """Add one turn's usage to the given day (default: today)."""
        prompt = prompt_tokens or 0
        completion = completion_tokens or 0
        total = total_tokens or (prompt + completion)
        cost = estimate_cost(model, prompt, completion, pricing=load_pricing(self._pricing_path))
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

    def today(self, day: str | None = None) -> dict:
        """Snapshot of one day's totals (default: today), plus the date."""
        day = day or _today()
        with self._lock:
            d = self._days.get(day) or _blank()
            return {
                "date": day,
                **{k: (dict(v) if isinstance(v, dict) else v) for k, v in d.items()},
            }
