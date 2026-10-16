"""MarketBoard data conversion and backend source requests."""

import json

import httpx

from assistant.cards.bundled.marketboard import (
    build_board,
    fetch_quotes,
    market_state,
    normalize_spark,
    quote_from_meta,
)
from tests.support.http import client


def test_normalize_spark_scales_to_0_100_and_downsamples():
    out = normalize_spark([10, 20, 30, 40, 50], n=16)
    assert out[0] == 0 and out[-1] == 100  # min → 0, max → 100
    assert all(0 <= v <= 100 for v in out)

    long = list(range(100))
    assert len(normalize_spark(long, n=16)) == 16  # downsampled to n


def test_normalize_spark_handles_sparse_or_flat():
    assert normalize_spark([]) == []
    assert normalize_spark([None, None]) == []
    assert normalize_spark([5]) == []  # need ≥2 points
    assert normalize_spark([7, 7, 7]) == [0, 0, 0]  # flat: no divide-by-zero


def test_market_state_from_trading_period():
    base = {"regularMarketTime": 1500}
    assert (
        market_state({**base, "currentTradingPeriod": {"regular": {"start": 1000, "end": 2000}}})
        == "open"
    )
    assert (
        market_state({**base, "currentTradingPeriod": {"regular": {"start": 1600, "end": 2000}}})
        == "closed"
    )
    assert (
        market_state({**base, "currentTradingPeriod": {"pre": {"start": 1400, "end": 1600}}})
        == "pre"
    )
    assert (
        market_state({**base, "currentTradingPeriod": {"post": {"start": 1400, "end": 1600}}})
        == "after"
    )
    # Unknown when the data isn't present — we never claim a state we can't prove.
    assert market_state({"regularMarketTime": 1500}) == ""
    assert market_state({"currentTradingPeriod": {"regular": {"start": 1, "end": 2}}}) == ""


def test_quote_from_meta_computes_change_and_optional_fields():
    meta = {
        "symbol": "AAPL",
        "shortName": "Apple Inc.",
        "regularMarketPrice": 110.0,
        "chartPreviousClose": 100.0,
        "currency": "USD",
        "fullExchangeName": "NasdaqGS",
        "regularMarketDayLow": 99.0,
        "regularMarketDayHigh": 112.0,
        "regularMarketTime": 1500,
        "currentTradingPeriod": {"regular": {"start": 1000, "end": 2000}},
    }
    q = quote_from_meta(meta, [100, 110])
    assert q["symbol"] == "AAPL" and q["name"] == "Apple Inc."
    assert q["change"] == 10.0 and q["changePercent"] == 10.0
    assert q["currency"] == "USD" and q["exchange"] == "NasdaqGS"
    assert q["dayLow"] == 99.0 and q["dayHigh"] == 112.0
    assert q["state"] == "open" and q["spark"] == [0, 100]


def test_quote_from_meta_missing_prev_close_is_safe():
    q = quote_from_meta({"symbol": "X", "regularMarketPrice": 5.0, "chartPreviousClose": 0}, [])
    assert q["change"] is None and q["changePercent"] is None  # no divide-by-zero
    assert "spark" not in q and "dayLow" not in q  # optionals omitted


def test_build_board_omits_currency_and_status_when_mixed():
    us = {
        "symbol": "AAPL",
        "shortName": "Apple",
        "regularMarketPrice": 110.0,
        "chartPreviousClose": 100.0,
        "currency": "USD",
        "regularMarketTime": 1500,
        "currentTradingPeriod": {"regular": {"start": 1000, "end": 2000}},
    }
    au = {
        "symbol": "BHP.AX",
        "shortName": "BHP",
        "regularMarketPrice": 50.0,
        "chartPreviousClose": 49.0,
        "currency": "AUD",
        "regularMarketTime": 1500,
        "currentTradingPeriod": {"regular": {"start": 1, "end": 2}},
    }  # closed (time past end)

    mixed = build_board([(us, [100, 110]), (au, [49, 50])], "Global")
    assert mixed["title"] == "Global" and mixed["source"] == "Yahoo Finance"
    assert "currency" not in mixed  # USD + AUD → omitted
    assert "status" not in mixed  # open + closed → omitted
    assert "asOf" in mixed and len(mixed["quotes"]) == 2

    # A single-market board DOES surface the shared currency + status.
    uniform = build_board([(us, [100, 110])], "Tech")
    assert uniform["currency"] == "USD" and uniform["status"] == "open"


def test_build_board_default_title():
    assert build_board([], "")["title"] == "Markets"


def test_market_source_resolves_names_and_preserves_the_requested_order():
    def respond(request):
        assert request.url.host == "query1.finance.yahoo.com"
        if request.url.path.endswith("/Toyota"):
            return httpx.Response(200, json={"chart": {"result": []}})
        if request.url.path.endswith("/search"):
            assert request.url.params["q"] == "Toyota"
            return httpx.Response(200, json={"quotes": [{"symbol": "7203.T"}]})
        symbol = request.url.path.rsplit("/", 1)[-1]
        assert symbol in {"AAPL", "7203.T"}
        assert request.url.params["range"] == "1d"
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "symbol": symbol,
                                "regularMarketPrice": 110,
                                "chartPreviousClose": 100,
                            },
                            "indicators": {"quote": [{"close": [100, 110]}]},
                        }
                    ]
                }
            },
        )

    with client(respond) as http:
        result = json.loads(fetch_quotes("Toyota,AAPL", "Watchlist", client=http))
        assert result["title"] == "Watchlist"
        assert [quote["symbol"] for quote in result["quotes"]] == ["7203.T", "AAPL"]
        assert all(quote["changePercent"] == 10 for quote in result["quotes"])
        assert not http.is_closed


def test_market_source_reports_missing_symbols_and_unavailable_data():
    with client(lambda request: httpx.Response(503)) as http:
        assert fetch_quotes("", client=http) == "No symbols requested."
        assert fetch_quotes("AAPL", client=http) == "No market data found for: AAPL."
