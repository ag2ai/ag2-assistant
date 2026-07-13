"""Finance/markets tool — one deterministic call for live quotes across global
exchanges, shaped for the A2UI MarketBoard.

Source: Yahoo Finance v8 chart (keyless). Covers US/AU/Europe/Asia/CN equities
and indices plus crypto under one symbol scheme, e.g. AAPL, BHP.AX, 7203.T,
VOD.L, ^AXJO, ^FTSE, ^GDAXI, ^N225, ^HSI, 000001.SS, BTC-USD. The agent passes
the symbols it wants; a plain name (or a slightly-off ticker) falls back to
Yahoo's search endpoint, so "ASX 200" or "Toyota" still resolve.

The returned JSON drops straight into a MarketBoard (title + quotes), so the
agent does not have to guess prices. The FIRST quote is the lead/featured.
"""

import json
from datetime import datetime, timezone
from urllib.parse import quote as urlquote

from ag2 import tool

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"
_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=1&newsCount=0"
_UA = {"User-Agent": "Mozilla/5.0"}  # Yahoo serves JSON to browser-like UAs
_MAX_SYMBOLS = 10


def normalize_spark(closes, n: int = 16) -> list[int]:
    """Downsample an intraday close series to ``n`` ints in 0..100 (shape only).

    Pure/deterministic. Normalised so the payload is tiny and the agent can copy
    it verbatim; the renderer scales it to the sparkline box. Returns ``[]`` when
    there isn't enough data to draw a line.
    """
    pts = [c for c in (closes or []) if c is not None]
    if len(pts) < 2:
        return []
    if len(pts) > n:
        step = (len(pts) - 1) / (n - 1)
        pts = [pts[round(i * step)] for i in range(n)]
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    return [round((p - lo) / span * 100) for p in pts]


def market_state(meta: dict) -> str:
    """Derive 'open' | 'pre' | 'after' | 'closed' from the trading period, or ''.

    Pure/deterministic. Compares the quote's ``regularMarketTime`` against the
    exchange's ``currentTradingPeriod`` windows — so we only ever claim a market
    state we can actually prove. Returns '' when the data isn't present.
    """
    t = meta.get("regularMarketTime")
    ctp = meta.get("currentTradingPeriod") or {}
    if t is None or not ctp:
        return ""

    def within(period: str) -> bool:
        p = ctp.get(period) or {}
        start, end = p.get("start"), p.get("end")
        return start is not None and end is not None and start <= t < end

    if within("regular"):
        return "open"
    if within("pre"):
        return "pre"
    if within("post"):
        return "after"
    return "closed"


def quote_from_meta(meta: dict, closes) -> dict:
    """Turn Yahoo chart ``meta`` (+ intraday closes) into one MarketBoard quote.

    Pure/deterministic. Price/change/percent are numbers; optional day range,
    normalised spark, and trading state are added only when known.
    """
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose")
    if prev is None:
        prev = meta.get("previousClose")

    if price is None or not prev:
        change = change_pct = None
    else:
        change = round(price - prev, 2)
        change_pct = round((price - prev) / prev * 100, 2)

    q: dict = {
        "symbol": meta.get("symbol", ""),
        "name": meta.get("shortName") or meta.get("longName") or meta.get("symbol", ""),
        "price": price,
        "change": change,
        "changePercent": change_pct,
        "currency": meta.get("currency", ""),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
    }
    low, high = meta.get("regularMarketDayLow"), meta.get("regularMarketDayHigh")
    if low is not None and high is not None:
        q["dayLow"], q["dayHigh"] = low, high
    spark = normalize_spark(closes)
    if spark:
        q["spark"] = spark
    state = market_state(meta)
    if state:
        q["state"] = state
    return q


def build_board(results: list[tuple[dict, list]], title: str = "") -> dict:
    """Assemble MarketBoard-ready fields from per-symbol (meta, closes) pairs.

    Pure/deterministic. Board-level ``currency`` and ``status`` are set ONLY when
    every quote agrees (a mixed-exchange board honestly shows neither); ``asOf``
    is the most recent quote time as ISO-8601.
    """
    quotes = [quote_from_meta(m, c) for (m, c) in results]
    board: dict = {"title": title or "Markets", "source": "Yahoo Finance", "quotes": quotes}

    currencies = {q["currency"] for q in quotes if q.get("currency")}
    if len(currencies) == 1:
        board["currency"] = next(iter(currencies))

    states = {q["state"] for q in quotes if q.get("state")}
    if len(states) == 1:
        board["status"] = next(iter(states))

    times = [m.get("regularMarketTime") for (m, _) in results if m.get("regularMarketTime")]
    if times:
        board["asOf"] = datetime.fromtimestamp(max(times), tz=timezone.utc).isoformat()
    return board


def _chart(client, symbol: str):
    """Fetch one symbol's chart → (meta, closes), or None on miss."""
    try:
        r = client.get(_CHART.format(sym=urlquote(symbol.strip())), headers=_UA, timeout=15.0)
        r.raise_for_status()
        result = (r.json().get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        if meta.get("regularMarketPrice") is None:
            return None
        closes = ((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        return meta, closes
    except Exception:
        return None


def _resolve(client, query: str) -> str | None:
    """Yahoo search → the top symbol for a plain name / fuzzy ticker."""
    try:
        r = client.get(_SEARCH.format(q=urlquote(query.strip())), headers=_UA, timeout=15.0)
        r.raise_for_status()
        for hit in r.json().get("quotes") or []:
            if hit.get("symbol"):
                return hit["symbol"]
    except Exception:
        pass
    return None


def _fetch_one(client, item: str):
    """Resolve one requested symbol/name to (meta, closes), or None on miss.

    Tries the symbol as-is, then falls back to Yahoo search for a plain name or
    fuzzy ticker. Self-contained so a whole board's symbols can be fetched
    concurrently (Yahoo calls are independent and httpx.Client is thread-safe).
    """
    hit = _chart(client, item)
    if hit is None:
        resolved = _resolve(client, item)
        if resolved:
            hit = _chart(client, resolved)
    return hit


@tool
def get_quotes(symbols: str, title: str = "") -> str:
    """Get live market quotes for stocks, ETFs, indices, or crypto across global exchanges.

    Start here for any markets/stocks/shares/ETFs/funds/indices/crypto request — it is
    the fast, reliable path for prices. Search the web for anything it does not cover
    (news, analysis, fundamentals). Pass the Yahoo symbols you want
    (US tickers like AAPL; suffixed international tickers like BHP.AX, 7203.T, VOD.L;
    indices like ^AXJO, ^FTSE, ^GDAXI, ^N225, ^HSI, 000001.SS; crypto like BTC-USD).
    Plain names ("ASX 200", "Toyota") are resolved via search if a symbol misses.

    Render the result as a MarketBoard from `title` + `quotes`; the FIRST quote is
    the lead/featured instrument. Use the prices for your short prose.

    Args:
        symbols: Comma-separated symbols or names, most important first.
        title: Optional board heading, e.g. "Technology", "Asian Markets", "Watchlist".

    Returns:
        JSON string: {"title","source","currency"?,"status"?,"asOf"?,
        "quotes":[{"symbol","name","price","change","changePercent","currency",
        "exchange","dayLow"?,"dayHigh"?,"spark"?,"state"?}, …]}.
    """
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    wanted = [s.strip() for s in str(symbols).split(",") if s.strip()][:_MAX_SYMBOLS]
    if not wanted:
        return "No symbols requested."

    # The per-symbol fetches are independent, so run them concurrently rather than
    # in series — a 10-symbol board is ~10× faster and won't stack the 15s timeouts.
    # httpx.Client is safe to share across threads; map() preserves request order.
    results: list[tuple[dict, list]] = []
    try:
        with httpx.Client(follow_redirects=True) as client:
            with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
                for hit in pool.map(lambda item: _fetch_one(client, item), wanted):
                    if hit is not None:
                        results.append(hit)
    except httpx.HTTPError as e:
        return f"Could not get market data: {e}"

    if not results:
        return f"No market data found for: {symbols}."

    return json.dumps(build_board(results, title), ensure_ascii=False)
