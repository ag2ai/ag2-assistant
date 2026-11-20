"""Cost & activity ledger: token tally, cost estimation, persistence."""

from pathlib import Path

import pytest
from ag2.events import Usage

from assistant.usage import UsageLedger, estimate_cost, load_pricing


def test_estimate_cost_known_and_unknown_model():
    # gemini-3.6-flash priced at (1.50, 7.50) per 1M tokens
    cost = estimate_cost(
        "gemini-3.6-flash", 1_000_000, 1_000_000, pricing=load_pricing(Path("nope.json"))
    )
    assert cost == 1.50 + 7.50
    assert (
        estimate_cost("some-unlisted-model", 1000, 1000, pricing=load_pricing(Path("nope.json")))
        is None
    )  # tokens-only


def test_ledger_accumulates_per_day(tmp_path):
    led = UsageLedger(tmp_path / "usage.json", pricing_path=tmp_path / "pricing.json")
    led.record("gemini-3.6-flash", 100, 50, 150, day="2026-06-21")
    led.record("gemini-3.6-flash", 200, 100, 300, day="2026-06-21")
    t = led.today(day="2026-06-21")
    assert t["prompt"] == 300 and t["completion"] == 150 and t["total"] == 450
    assert t["priced"] is True and t["cost"] > 0
    assert "gemini-3.6-flash" in t["by_model"]


def test_ledger_separates_days(tmp_path):
    led = UsageLedger(tmp_path / "usage.json", pricing_path=tmp_path / "pricing.json")
    led.record("gemini-3.6-flash", 100, 50, 150, day="2026-06-20")
    led.record("gemini-3.6-flash", 999, 999, 1998, day="2026-06-21")
    assert led.today(day="2026-06-20")["total"] == 150


def test_ledger_persists(tmp_path):
    p = tmp_path / "usage.json"
    UsageLedger(p, pricing_path=tmp_path / "pricing.json").record(
        "gemini-3.6-flash", 100, 50, 150, day="2026-06-21"
    )
    # a fresh ledger over the same file sees the persisted total
    assert (
        UsageLedger(p, pricing_path=tmp_path / "pricing.json").today(day="2026-06-21")["total"]
        == 150
    )


def test_unpriced_model_tracks_tokens_without_cost(tmp_path):
    led = UsageLedger(tmp_path / "usage.json", pricing_path=tmp_path / "pricing.json")
    led.record("mystery-model", 100, 50, 150, day="2026-06-21")
    t = led.today(day="2026-06-21")
    assert t["total"] == 150 and t["priced"] is False and t["cost"] == 0.0


def test_anthropic_cache_hits_are_priced_at_the_cache_read_rate(tmp_path):
    """The review's case: 1,000 uncached + 9,000 cache-read tokens at a $3/MTok input
    rate cost $0.0057, not $0.0300 — prompt_tokens includes the cache reads."""
    (tmp_path / "pricing.json").write_text('{"claude-sonnet": [3.0, 15.0]}')
    pricing = load_pricing(tmp_path / "pricing.json")
    cost = estimate_cost("claude-sonnet-4-6", 10_000, 0, pricing=pricing, cache_read=9_000)
    assert cost == pytest.approx(0.0057)


def test_anthropic_cache_writes_use_the_write_rates():
    pricing = load_pricing(Path("nope.json"))
    # claude-opus at $5/MTok input: 5-minute writes 1.25x, 1-hour writes 2x.
    cost = estimate_cost(
        "claude-opus-5",
        3_000_000,
        0,
        pricing=pricing,
        cache_write=2_000_000,
        cache_write_extended=1_000_000,
    )
    assert cost == pytest.approx(1 * 5.0 + 1 * 6.25 + 1 * 10.0)


def test_a_model_without_cache_rates_prices_cached_tokens_as_input():
    pricing = load_pricing(Path("nope.json"))
    cached = estimate_cost("gpt-5", 1_000_000, 0, pricing=pricing, cache_read=900_000)
    assert cached == estimate_cost("gpt-5", 1_000_000, 0, pricing=pricing)


def test_an_override_can_set_cache_rates(tmp_path):
    (tmp_path / "pricing.json").write_text(
        '{"gpt-5": {"input": 1.25, "output": 10.0, "cache_read": 0.125}}'
    )
    pricing = load_pricing(tmp_path / "pricing.json")
    assert estimate_cost("gpt-5", 1_000_000, 0, pricing=pricing, cache_read=1_000_000) == (
        pytest.approx(0.125)
    )


def test_record_usage_carries_the_cache_breakdown(tmp_path):
    (tmp_path / "pricing.json").write_text('{"claude-sonnet": [3.0, 15.0]}')
    led = UsageLedger(tmp_path / "usage.json", pricing_path=tmp_path / "pricing.json")
    led.record_usage(
        "claude-sonnet-4-6", Usage(prompt_tokens=10_000, cache_read_input_tokens=9_000)
    )
    assert led.today()["cost"] == pytest.approx(0.0057)
