"""MarketBoard's global equity, index and crypto quotes from Yahoo Finance."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from urllib.parse import quote as urlquote

import httpx

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"
_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=1&newsCount=0"
_UA = {"User-Agent": "Mozilla/5.0"}  # Yahoo serves JSON to browser-like UAs
_MAX_SYMBOLS = 10


def normalize_spark(closes, n: int = 16) -> list[int]:
    """Downsample intraday closes to at most n points normalized to 0..100."""
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
    """Derive open, pre, after or closed from trading periods, or empty when unknown."""
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
    """Convert Yahoo chart metadata and intraday closes into one quote."""
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
    """Assemble MarketBoard fields, including a shared currency and status when available."""
    quotes = [quote_from_meta(m, c) for (m, c) in results]
    board: dict = {"title": title or "Markets", "source": "Yahoo Finance", "quotes": quotes}

    currencies = {q["currency"] for q in quotes if q.get("currency")}
    if len(currencies) == 1:
        board["currency"] = next(iter(currencies))

    states = {q["state"] for q in quotes if q.get("state")}
    if len(states) == 1:
        board["status"] = next(iter(states))

    times = [t for (m, _) in results if (t := m.get("regularMarketTime"))]
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
    """Fetch a symbol's chart, resolving plain names through Yahoo search on a miss."""
    hit = _chart(client, item)
    if hit is None:
        resolved = _resolve(client, item)
        if resolved:
            hit = _chart(client, resolved)
    return hit


def fetch_quotes(symbols: str, title: str = "", *, client: httpx.Client | None = None) -> str:
    """Fetch MarketBoard fields as JSON, preserving the requested symbol order."""
    wanted = [s.strip() for s in str(symbols).split(",") if s.strip()][:_MAX_SYMBOLS]
    if not wanted:
        return "No symbols requested."

    # Independent symbol requests share the thread-safe client and preserve their order.
    results: list[tuple[dict, list]] = []
    try:
        with (
            nullcontext(client) if client is not None else httpx.Client(follow_redirects=True)
        ) as http:
            with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
                for hit in pool.map(lambda item: _fetch_one(http, item), wanted):
                    if hit is not None:
                        results.append(hit)
    except httpx.HTTPError as e:
        return f"Could not get market data: {e}"

    if not results:
        return f"No market data found for: {symbols}."

    return json.dumps(build_board(results, title), ensure_ascii=False)
