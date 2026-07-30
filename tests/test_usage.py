"""Cost & activity ledger: token tally, cost estimation, persistence."""

from assistant.usage import UsageLedger, estimate_cost


def test_estimate_cost_known_and_unknown_model():
    # gemini-3.6-flash priced at (1.5, 7.50) per 1M tokens
    cost = estimate_cost("gemini-3.6-flash", 1_000_000, 1_000_000)
    assert cost == 1.50 + 7.50
    assert estimate_cost("some-unlisted-model", 1000, 1000) is None  # tokens-only


def test_ledger_accumulates_per_day(tmp_path):
    led = UsageLedger(path=tmp_path / "usage.json")
    led.record("gemini-3.6-flash", 100, 50, 150, day="2026-06-21")
    led.record("gemini-3.6-flash", 200, 100, 300, day="2026-06-21")
    t = led.today(day="2026-06-21")
    assert t["prompt"] == 300 and t["completion"] == 150 and t["total"] == 450
    assert t["priced"] is True and t["cost"] > 0
    assert "gemini-3.6-flash" in t["by_model"]


def test_ledger_separates_days(tmp_path):
    led = UsageLedger(path=tmp_path / "usage.json")
    led.record("gemini-3.6-flash", 100, 50, 150, day="2026-06-20")
    led.record("gemini-3.6-flash", 999, 999, 1998, day="2026-06-21")
    assert led.today(day="2026-06-20")["total"] == 150


def test_ledger_persists(tmp_path):
    p = tmp_path / "usage.json"
    UsageLedger(path=p).record("gemini-3.6-flash", 100, 50, 150, day="2026-06-21")
    # a fresh ledger over the same file sees the persisted total
    assert UsageLedger(path=p).today(day="2026-06-21")["total"] == 150


def test_unpriced_model_tracks_tokens_without_cost(tmp_path):
    led = UsageLedger(path=tmp_path / "usage.json")
    led.record("mystery-model", 100, 50, 150, day="2026-06-21")
    t = led.today(day="2026-06-21")
    assert t["total"] == 150 and t["priced"] is False and t["cost"] == 0.0
