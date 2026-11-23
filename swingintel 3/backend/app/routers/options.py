"""
Options Desk: data layer + signal endpoints.

Data sources (all free, all with real limits):
  * yfinance        quotes, daily/intraday bars, option chains. Yahoo options
                    data is delayed (usually about 15 minutes) and carries no
                    true buy/sell tape. Every response says how old each
                    piece of data is, and entries are blocked when quotes are
                    stale or the market is closed.
  * Finnhub         company news and insider filings (FINNHUB_KEY), SEC Form 4.
  * NewsAPI         fallback news (NEWSAPI_KEY). The free plan delays articles
                    by 24 hours, so it is only a fallback.
  * Google News RSS no-key source for political headlines (minutes old).

Nothing here executes trades. It produces a scored idea plus an entry, stop and
target plan that the paper-trade journal can record and grade.

Upgrade path for faster data: replace _history / _chain with a Polygon or
Tradier call. The engine and the endpoints do not care where rows come from.
"""

from __future__ import annotations

import os
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query

from app import options_engine as oe
from app.db import get_connection, get_database_url
from app.routers.news import score_headline

router = APIRouter()

ET_TZ = ZoneInfo("America/New_York")
FRESH_LIMIT_S = 20 * 60

# ---------------------------------------------------------------------------
# Tiny TTL cache. Serves stale data (with its true age) if a refresh fails, so
# a Yahoo rate limit degrades the display instead of breaking it. Stale data
# still trips the staleness gate before any entry is allowed.
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[object, float]] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, fn, refresh: bool = False):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and not refresh and now - hit[1] < ttl:
        return hit[0], hit[1]
    try:
        val = fn()
    except Exception:
        if hit:
            return hit[0], hit[1]
        raise
    ts = time.time()
    if isinstance(val, list) and not val and ttl > 120:
        ts -= ttl - 120  # an empty result is retried after 2 minutes, not held for the full TTL
    with _lock:
        _cache[key] = (val, ts)
    return val, ts


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if x != x else x  # NaN check


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat()


def market_open_now(now: datetime | None = None) -> bool:
    n = (now or datetime.now(timezone.utc)).astimezone(ET_TZ)
    return n.weekday() < 5 and dtime(9, 30) <= n.time() < dtime(16, 0)  # holidays are caught by the quote-age check


# ---------------------------------------------------------------------------
# yfinance data layer
# ---------------------------------------------------------------------------


def _history(ticker: str, period: str, interval: str) -> dict:
    df = yf.Ticker(ticker).history(period=period, interval=interval)
    if df is None or df.empty:
        return {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    df = df.dropna(subset=["Close"])
    return {
        "t": [i.to_pydatetime().astimezone(timezone.utc).isoformat() for i in df.index],
        "o": [float(x) for x in df["Open"]], "h": [float(x) for x in df["High"]],
        "l": [float(x) for x in df["Low"]], "c": [float(x) for x in df["Close"]],
        "v": [float(x) for x in df["Volume"]],
    }


def get_daily(ticker: str, refresh=False, period="1y"):
    return cached(f"d:{ticker}:{period}", 300, lambda: _history(ticker, period, "1d"), refresh)


def get_intraday(ticker: str, refresh=False):
    def go():
        h = _history(ticker, "1d", "1m")
        if not h["c"]:
            h = _history(ticker, "5d", "5m")
        return h
    return cached(f"i:{ticker}", 45, go, refresh)


# ---- Option chain providers -------------------------------------------------
# Yahoo often refuses option requests from cloud server IPs (Render included), while
# price history still works. If TRADIER_TOKEN is set (free sandbox account, 15 minute
# delayed data with greeks) it is tried first, and Yahoo is the fallback.
TRADIER_BASE = "https://sandbox.tradier.com/v1"
_chain_errors: dict[str, str] = {}
_provider_used: dict[str, str] = {}


def tradier_configured() -> bool:
    return bool(os.environ.get("TRADIER_TOKEN"))


def _tradier_get(path: str, params: dict) -> dict:
    base = os.environ.get("TRADIER_BASE_URL", TRADIER_BASE)
    r = requests.get(f"{base}{path}", params=params, timeout=12,
                     headers={"Authorization": f"Bearer {os.environ['TRADIER_TOKEN']}", "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _tradier_expirations(ticker: str) -> list[str]:
    j = _tradier_get("/markets/options/expirations", {"symbol": ticker, "includeAllRoots": "true"})
    return sorted(str(d) for d in _as_list((j.get("expirations") or {}).get("date")))


def _tradier_chain(ticker: str, expiry: str) -> list[dict]:
    j = _tradier_get("/markets/options/chains", {"symbol": ticker, "expiration": expiry, "greeks": "true"})
    out = []
    for o in _as_list((j.get("options") or {}).get("option")):
        g = o.get("greeks") or {}
        iv = _f(g.get("mid_iv")) or _f(g.get("smv_vol")) or None
        td = _f(o.get("trade_date"))
        out.append({
            "right": o.get("option_type"), "strike": _f(o.get("strike")), "expiry": o.get("expiration_date") or expiry,
            "bid": _f(o.get("bid")) or 0.0, "ask": _f(o.get("ask")) or 0.0, "last": _f(o.get("last")) or 0.0,
            "volume": int(_f(o.get("volume")) or 0), "oi": int(_f(o.get("open_interest")) or 0), "iv": iv,
            "last_trade": datetime.fromtimestamp(td / 1000, timezone.utc).isoformat() if td else None,
            "contract_symbol": o.get("symbol"),
        })
    return [r for r in out if r["strike"] is not None and r["right"] in ("call", "put")]


def _yahoo_expirations(ticker: str) -> list[str]:
    exps = list(yf.Ticker(ticker).options or [])
    if not exps:
        raise RuntimeError("Yahoo returned no option expirations (it often blocks cloud server IPs)")
    return exps


def _yahoo_chain(ticker: str, expiry: str) -> list[dict]:
    ch = yf.Ticker(ticker).option_chain(expiry)
    return _rows_from_df(ch.calls, "call", expiry) + _rows_from_df(ch.puts, "put", expiry)


def _providers(kind: str):
    p = []
    if tradier_configured():
        p.append(("tradier", _tradier_expirations if kind == "x" else _tradier_chain))
    p.append(("yahoo", _yahoo_expirations if kind == "x" else _yahoo_chain))
    return p


def get_expirations(ticker: str, refresh=False):
    def go():
        errors = []
        for name, fn in _providers("x"):
            try:
                exps = fn(ticker)
                if exps:
                    _provider_used[ticker] = name
                    _chain_errors.pop(ticker, None)
                    return exps
            except Exception as e:
                errors.append(f"{name}: {str(e)[:160]}")
        _chain_errors[ticker] = "; ".join(errors) or "no option data"
        return []
    return cached(f"x:{ticker}", 600, go, refresh)


def get_chain(ticker: str, expiry: str, refresh=False):
    def go():
        order = _providers("c")
        used = _provider_used.get(ticker)
        order.sort(key=lambda p: p[0] != used)  # keep using the provider that gave the expirations
        last = None
        for name, fn in order:
            try:
                rows = fn(ticker, expiry)
                if rows:
                    return rows
            except Exception as e:
                last = e
        raise last or RuntimeError("empty option chain")
    return cached(f"c:{ticker}:{expiry}", 90, go, refresh)


def get_earnings_date(ticker: str):
    def go():
        cal = yf.Ticker(ticker).calendar
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if not ed:
            return None
        ed = ed[0] if isinstance(ed, (list, tuple)) else ed
        return ed.isoformat() if hasattr(ed, "isoformat") else str(ed)
    try:
        return cached(f"e:{ticker}", 6 * 3600, go)[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# News / policy / insider
# ---------------------------------------------------------------------------


def _google_rss(query: str, limit: int = 15) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}+when:1d&hl=en-US&gl=US&ceid=US:en"
    r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0 SwingIntel"})
    r.raise_for_status()
    out = []
    for item in ET.fromstring(r.content).iterfind(".//item"):
        title = (item.findtext("title") or "").strip()
        try:
            t = parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc).isoformat()
        except Exception:
            continue
        out.append({"headline": title, "source": (item.findtext("source") or "Google News"), "time": t, "url": item.findtext("link")})
        if len(out) >= limit:
            break
    return out


def _finnhub_company_news(ticker: str) -> list[dict]:
    key = os.environ.get("FINNHUB_KEY")
    if not key:
        raise RuntimeError("FINNHUB_KEY not set")
    today = date.today()
    r = requests.get("https://finnhub.io/api/v1/company-news", timeout=8,
                     params={"symbol": ticker, "from": (today - timedelta(days=2)).isoformat(), "to": today.isoformat(), "token": key})
    r.raise_for_status()
    return [{"headline": a.get("headline"), "source": a.get("source"), "url": a.get("url"),
             "time": datetime.fromtimestamp(a["datetime"], timezone.utc).isoformat()}
            for a in r.json()[:25] if a.get("headline") and a.get("datetime")]


def _newsapi(q: str, page_size: int = 20) -> list[dict]:
    key = os.environ.get("NEWSAPI_KEY")
    if not key:
        raise RuntimeError("NEWSAPI_KEY not set")
    r = requests.get("https://newsapi.org/v2/everything", timeout=8,
                     params={"q": q, "sortBy": "publishedAt", "language": "en", "pageSize": page_size, "apiKey": key})
    r.raise_for_status()
    return [{"headline": a.get("title"), "source": (a.get("source") or {}).get("name"), "url": a.get("url"), "time": a.get("publishedAt")}
            for a in r.json().get("articles", []) if a.get("title")]


def ticker_headlines(ticker: str, refresh=False):
    def go():
        for name, fn in (("finnhub", lambda: _finnhub_company_news(ticker)),
                         ("google_rss", lambda: _google_rss(f"{ticker} stock")),
                         ("newsapi", lambda: _newsapi(ticker))):
            try:
                items = fn()
                if items:
                    return {"source": name, "items": items}
            except Exception:
                continue
        return {"source": None, "items": []}
    return cached(f"n:{ticker}", 300, go, refresh)


def political_headlines(refresh=False):
    """One shared fetch for all tickers, refreshed at most every 5 minutes."""
    def go():
        items, sources = [], []
        for name, fn in (("google_rss", lambda: _google_rss("Trump tariffs OR sanctions OR \"executive order\"", 20)),
                         ("google_rss", lambda: _google_rss("White House trade OR chips OR energy OR drug pricing", 20)),
                         ("newsapi", lambda: _newsapi("Trump AND (tariff OR \"executive order\" OR sanctions)", 20))):
            try:
                got = fn()
                if got:
                    items += got
                    sources.append(name)
            except Exception:
                continue
        seen, uniq = set(), []
        for it in sorted(items, key=lambda x: x["time"] or "", reverse=True):
            k = (it["headline"] or "").lower()[:80]
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        return {"sources": sorted(set(sources)), "items": uniq[:60]}
    return cached("pol", 300, go, refresh)


def insider_factor(ticker: str) -> dict:
    """Open-market purchases (code P) vs sales (code S) in the last 90 days from SEC Form 4 via Finnhub.
    Buys are rarer and more informative, so a buy counts 3x a sale."""
    key = os.environ.get("FINNHUB_KEY")
    if not key:
        return {"score": 0.0, "available": False, "detail": "FINNHUB_KEY not set"}

    def go():
        r = requests.get("https://finnhub.io/api/v1/stock/insider-transactions", params={"symbol": ticker, "token": key}, timeout=8)
        r.raise_for_status()
        return r.json().get("data", [])
    try:
        data, ts = cached(f"ins:{ticker}", 6 * 3600, go)
    except Exception as e:
        return {"score": 0.0, "available": False, "detail": f"Finnhub error: {e}"}
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    recent = [t for t in data if (t.get("transactionDate") or "") >= cutoff and t.get("transactionCode") in ("P", "S")]
    buys = sum(1 for t in recent if t["transactionCode"] == "P")
    sells = sum(1 for t in recent if t["transactionCode"] == "S")
    if not buys and not sells:
        return {"score": 0.0, "available": True, "detail": "No open-market insider trades in 90 days", "as_of": _iso(ts)}
    score = (3 * buys - sells) / (3 * buys + sells)
    return {"score": round(score, 3), "available": True, "buys": buys, "sells": sells,
            "detail": f"{buys} open-market buys, {sells} sells in 90 days (Form 4)", "as_of": _iso(ts)}


def _score_items(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        s = score_headline(it["headline"] or "")
        out.append({**it, **s})
    return out


# ---------------------------------------------------------------------------
# IV history (our own, so IV rank becomes real after a few weeks)
# ---------------------------------------------------------------------------


def _iv_history(ticker: str) -> list[float]:
    if not get_database_url():
        return []
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT atm_iv FROM iv_history WHERE ticker=%s ORDER BY day DESC LIMIT 252", (ticker,))
            return [r["atm_iv"] for r in cur.fetchall()]
    except Exception:
        return []


def _log_iv(ticker: str, iv: float):
    if not get_database_url() or not market_open_now():
        return
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO iv_history (ticker, day, atm_iv) VALUES (%s, %s, %s) "
                "ON CONFLICT (ticker, day) DO UPDATE SET atm_iv = EXCLUDED.atm_iv",
                (ticker, datetime.now(ET_TZ).date(), iv),
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expiry_T(expiry: str, now: datetime) -> tuple[int, float]:
    exp_dt = datetime.combine(date.fromisoformat(expiry), dtime(16, 0), tzinfo=ET_TZ)
    secs = max((exp_dt - now).total_seconds(), 3600)
    dte = (date.fromisoformat(expiry) - now.astimezone(ET_TZ).date()).days
    return dte, secs / (365 * 86400)


def choose_expiry(expirations: list[str], now: datetime, dte_min: int, dte_max: int) -> str | None:
    today = now.astimezone(ET_TZ).date()
    pairs = [(e, (date.fromisoformat(e) - today).days) for e in expirations]
    in_range = [p for p in pairs if dte_min <= p[1] <= dte_max]
    if in_range:
        return min(in_range, key=lambda p: abs(p[1] - 30))[0]
    later = [p for p in pairs if p[1] >= 7]
    return min(later, key=lambda p: p[1])[0] if later else (pairs[-1][0] if pairs else None)


def _atm_stats(rows: list[dict], spot: float, T: float) -> dict:
    """ATM strike, ATM IV and straddle-implied expected move for one expiry."""
    strikes = sorted({r["strike"] for r in rows})
    if not strikes:
        return {}
    k = min(strikes, key=lambda s: abs(s - spot))
    legs = {r["right"]: r for r in rows if r["strike"] == k}
    ivs = []
    mids = {}
    for right, r in legs.items():
        mid = (r["bid"] + r["ask"]) / 2 if r["bid"] > 0 and r["ask"] > 0 else None
        mids[right] = mid
        iv = r["iv"] if oe.sane_iv(r["iv"]) else (oe.implied_vol(mid, spot, k, T, right) if mid else None)
        if oe.sane_iv(iv):
            ivs.append(iv)
    atm_iv = sum(ivs) / len(ivs) if ivs else None
    straddle = (mids.get("call") or 0) + (mids.get("put") or 0) if mids.get("call") and mids.get("put") else None
    em = straddle if straddle else (spot * atm_iv * (T ** 0.5) if atm_iv else None)
    newest = None
    for r in rows:
        if abs(r["strike"] - k) <= 3 * max(1.0, (strikes[1] - strikes[0]) if len(strikes) > 1 else 1.0) and r["last_trade"]:
            newest = r["last_trade"] if newest is None or r["last_trade"] > newest else newest
    return {"atm_strike": k, "atm_iv": atm_iv, "straddle": straddle, "expected_move": em, "newest_trade": newest}


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


def compute_signal(ticker: str, dte_min: int = 14, dte_max: int = 45, refresh: bool = False) -> dict:
    ticker = ticker.upper()
    now = datetime.now(timezone.utc)
    is_open = market_open_now(now)

    daily, d_ts = get_daily(ticker, refresh)
    if len(daily["c"]) < 30:
        raise HTTPException(status_code=404, detail=f"Not enough price history for '{ticker}'")
    intra, i_ts = get_intraday(ticker, refresh)

    # Kick off the key-based HTTP fetches while yfinance runs sequentially.
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_news = ex.submit(ticker_headlines, ticker, refresh)
        f_pol = ex.submit(political_headlines, refresh)
        f_ins = ex.submit(insider_factor, ticker)

        spot = intra["c"][-1] if intra["c"] else daily["c"][-1]
        quote_ts = datetime.fromisoformat(intra["t"][-1]) if intra["t"] else datetime.fromisoformat(daily["t"][-1])
        quote_age = (now - quote_ts).total_seconds()
        # An intraday bar is stamped at its start. Remove one bar length so a live 1m bar does not look 60s old.
        quote_fresh = oe.staleness(max(0.0, quote_age - 60), is_open, FRESH_LIMIT_S)

        closes = daily["c"][:]
        if intra["c"]:
            closes[-1] = spot if daily["t"][-1][:10] == intra["t"][-1][:10] else closes[-1]
        highs, lows = daily["h"], daily["l"]
        tech = oe.trend_score(closes)
        atr_v = oe.atr(highs, lows, closes)
        rsi_v = oe.rsi(closes)
        levels = oe.swing_levels(highs, lows, spot)
        hv_s = oe.hv_series(closes)
        hv20 = hv_s[-1] if hv_s else None
        hv_pct = oe.percentile_rank(hv20, hv_s) if hv20 else None

        vwap = None
        if intra["t"]:
            day = intra["t"][-1][:10]
            idx = [i for i, t in enumerate(intra["t"]) if t[:10] == day]
            vol = sum(intra["v"][i] for i in idx)
            if vol > 0:
                vwap = sum((intra["h"][i] + intra["l"][i] + intra["c"][i]) / 3 * intra["v"][i] for i in idx) / vol

        # Options
        try:
            exps, _ = get_expirations(ticker, refresh)
        except Exception as e:
            exps = []
            _chain_errors[ticker] = str(e)[:200]
        chain_error = None if exps else (_chain_errors.get(ticker) or "no option expirations returned")
        expiry = choose_expiry(exps, now, dte_min, dte_max) if exps else None
        chain_rows, c_ts, atm, T, dte, pc, walls, pain, flow = [], None, {}, None, None, {}, {}, None, {"items": [], "count": 0, "bias": 0.0, "directional_notional": 0}
        if expiry:
            dte, T = _expiry_T(expiry, now)
            chain_rows, c_ts = get_chain(ticker, expiry, refresh)
            atm = _atm_stats(chain_rows, spot, T)
            oi_rows: dict[float, list[float]] = {}
            for r in chain_rows:
                cell = oi_rows.setdefault(r["strike"], [0.0, 0.0])
                cell[0 if r["right"] == "call" else 1] += r["oi"]
            oi_tuples = [(k, v[0], v[1]) for k, v in sorted(oi_rows.items())]
            pain, walls = oe.max_pain(oi_tuples), oe.oi_walls(oi_tuples, spot)
            pc = oe.put_call_ratios(chain_rows)
            flow_rows = chain_rows
            other = [e for e in exps if e != expiry and 0 <= (date.fromisoformat(e) - now.astimezone(ET_TZ).date()).days <= 21]
            if other:
                try:
                    flow_rows = chain_rows + get_chain(ticker, other[0], refresh)[0]
                except Exception:
                    pass
            flow = oe.unusual_flow(flow_rows)

        atm_iv = atm.get("atm_iv")
        history = _iv_history(ticker)
        ivr = oe.iv_rank(atm_iv, history) if atm_iv else None
        if atm_iv and dte and 14 <= dte <= 60:
            _log_iv(ticker, atm_iv)
        iv_vs_hv = (atm_iv / hv20) if atm_iv and hv20 else None
        iv_expensive = (ivr is not None and ivr >= 70) or (ivr is None and iv_vs_hv is not None and iv_vs_hv > 1.35)

        ed = get_earnings_date(ticker)
        earnings_in_window = False
        days_to_earn = None
        if ed:
            try:
                ed_d = date.fromisoformat(str(ed)[:10])
                days_to_earn = (ed_d - now.astimezone(ET_TZ).date()).days
                earnings_in_window = bool(expiry) and 0 <= days_to_earn <= (dte or 0)
            except Exception:
                pass

        # Factors
        news_pack, n_ts = f_news.result()
        n_items = _score_items(news_pack["items"])
        news_f = oe.news_score(n_items)
        news_f["source"] = news_pack["source"]
        news_f["headlines"] = sorted(n_items, key=lambda x: x["time"] or "", reverse=True)[:5]
        pol_pack, p_ts = f_pol.result()
        pol_f = oe.political_score(ticker, pol_pack["items"])
        pol_f["sources"] = pol_pack["sources"]
        pol_f["as_of"] = _iso(p_ts)
        ins_f = f_ins.result()

    spy, _ = get_daily("SPY", refresh, "6mo")
    try:
        vix, _ = get_daily("^VIX", refresh, "3mo")
    except Exception:
        vix = {"c": []}
    reg = oe.regime(spy["c"], vix["c"])

    flow_f = {"score": flow["bias"], "available": flow["directional_notional"] > 0,
              "detail": f"{flow['count']} unusual contracts, directional bias {flow['bias']:+.2f}", "as_of": _iso(c_ts)}
    if flow["count"] < 2:
        flow_f["score"] = round(flow_f["score"] * 0.5, 3)
    tech["as_of"] = quote_ts.isoformat()
    factors = {"trend": tech, "flow": flow_f, "news": news_f, "political": pol_f, "insider": ins_f, "regime": reg}

    comp = oe.composite(factors)
    ctx = {
        "iv_expensive": iv_expensive, "earnings_in_window": earnings_in_window, "liquidity_ok": True,
        "market_open": is_open, "data_fresh": quote_fresh["fresh"] or not is_open,
    }
    dec = oe.decide(comp, factors, ctx)

    if not exps and dec["direction"] != "no_trade":
        dec["actionable"] = False
        dec["warnings"].append(f"Options chain unavailable ({chain_error}). This is a directional view only, so no contract can be picked")
    plan = contract = None
    if dec["direction"] != "no_trade" and atr_v and expiry and T:
        plan = oe.trade_plan(dec["direction"], spot, atr_v, levels["supports"], levels["resistances"], atm.get("expected_move"), rsi_v)
        contract = oe.pick_contract([r for r in chain_rows if r["right"] == dec["direction"]], spot, T, dec["direction"], plan)
        if not contract.get("found"):
            dec["actionable"] = False
            dec["warnings"].append("No contract passed the liquidity and spread filters")
        elif contract["spread_pct"] > 10:
            dec["conviction"] = round(dec["conviction"] * 0.8)
            dec["warnings"].append(f"Wide bid/ask spread ({contract['spread_pct']}%), expect slippage")
        if plan["status"] == "wait":
            dec["warnings"].append("Do not chase: " + plan["note"])

    chain_age = None
    if atm.get("newest_trade"):
        chain_age = (now - datetime.fromisoformat(atm["newest_trade"])).total_seconds()

    return {
        "ticker": ticker, "spot": round(spot, 2), "generated_at": now.isoformat(),
        "meta": {
            "market_open": is_open,
            "quote": {**quote_fresh, "as_of": quote_ts.isoformat()},
            "chain": {"age_s": None if chain_age is None else int(chain_age), "fetched_at": _iso(c_ts),
                      "provider": _provider_used.get(ticker), "error": chain_error,
                      "note": "Yahoo options data is typically delayed about 15 minutes"},
            "news": {"fetched_at": _iso(n_ts), "source": news_pack["source"]},
            "political": {"fetched_at": _iso(p_ts), "sources": pol_pack["sources"]},
        },
        "direction": dec["direction"], "conviction": dec["conviction"], "actionable": dec["actionable"],
        "reasons": dec["reasons"], "warnings": dec["warnings"],
        "composite": comp, "factors": factors,
        "levels": {
            "atr": None if atr_v is None else round(atr_v, 2), "rsi": None if rsi_v is None else round(rsi_v, 1),
            "supports": levels["supports"], "resistances": levels["resistances"],
            "vwap": None if vwap is None else round(vwap, 2), "sma20": tech.get("sma20"), "sma50": tech.get("sma50"),
            "call_wall": walls.get("call_wall"), "put_wall": walls.get("put_wall"), "max_pain": pain,
        },
        "volatility": {
            "atm_iv": None if atm_iv is None else round(atm_iv, 3), "hv20": None if hv20 is None else round(hv20, 3),
            "hv_percentile": hv_pct, "iv_rank": ivr, "iv_rank_samples": len(history),
            "iv_vs_hv": None if iv_vs_hv is None else round(iv_vs_hv, 2),
            "expected_move": None if atm.get("expected_move") is None else round(atm["expected_move"], 2),
            "expected_move_pct": None if atm.get("expected_move") is None else round(atm["expected_move"] / spot * 100, 1),
            "straddle": None if atm.get("straddle") is None else round(atm["straddle"], 2),
            "iv_expensive": iv_expensive,
        },
        "expiry": {"date": expiry, "dte": dte},
        "expirations": [{"date": e, "dte": (date.fromisoformat(e) - now.astimezone(ET_TZ).date()).days} for e in exps[:12]],
        "flow": {**flow, "put_call": pc},
        "earnings": {"date": ed, "days": days_to_earn, "in_window": earnings_in_window},
        "plan": plan, "contract": contract,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/regime")
def get_regime(refresh: bool = False):
    spy, s_ts = get_daily("SPY", refresh, "6mo")
    try:
        vix, _ = get_daily("^VIX", refresh, "3mo")
    except Exception:
        vix = {"c": []}
    return {**oe.regime(spy["c"], vix["c"]), "as_of": _iso(s_ts)}


@router.get("/diag/{ticker}")
def diagnostics(ticker: str):
    """Why is option data missing? Tries each provider once and reports what happened."""
    t = ticker.upper()
    out: dict = {"tradier_configured": tradier_configured(), "providers": {}}
    for name, fn in _providers("x"):
        try:
            e = fn(t)
            out["providers"][name] = {"ok": bool(e), "expirations": len(e), "first": e[:3]}
        except Exception as ex:
            out["providers"][name] = {"ok": False, "error": str(ex)[:300]}
    try:
        r = requests.get(f"https://query2.finance.yahoo.com/v7/finance/options/{t}", timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        out["yahoo_raw_http_status"] = r.status_code
    except Exception as ex:
        out["yahoo_raw_http_status"] = f"error: {str(ex)[:120]}"
    return out


@router.get("/{ticker}/signal")
def get_signal(ticker: str, dte_min: int = Query(14, ge=1, le=120), dte_max: int = Query(45, ge=2, le=365), refresh: bool = False):
    try:
        return compute_signal(ticker, dte_min, dte_max, refresh)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not build signal for '{ticker}': {e}")


@router.get("/{ticker}/chain")
def get_chain_view(ticker: str, expiry: str, width: int = Query(8, ge=2, le=25), refresh: bool = False):
    """Strikes around the money for one expiry, with greeks, for the chain heatmap."""
    ticker = ticker.upper()
    try:
        rows, ts = get_chain(ticker, expiry, refresh)
        daily, _ = get_daily(ticker)
        intra, _ = get_intraday(ticker)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not load chain for '{ticker}' {expiry}: {e}")
    spot = intra["c"][-1] if intra["c"] else daily["c"][-1]
    now = datetime.now(timezone.utc)
    dte, T = _expiry_T(expiry, now)
    strikes = sorted({r["strike"] for r in rows})
    if not strikes:
        raise HTTPException(status_code=404, detail="Empty chain")
    atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    keep = set(strikes[max(0, atm_i - width): atm_i + width + 1])
    out: dict[float, dict] = {}
    for r in rows:
        if r["strike"] not in keep:
            continue
        mid = (r["bid"] + r["ask"]) / 2 if r["bid"] > 0 and r["ask"] > 0 else r["last"]
        iv = r["iv"] if oe.sane_iv(r["iv"]) else (oe.implied_vol(mid, spot, r["strike"], T, r["right"]) if mid else None)
        g = oe.bs_greeks(spot, r["strike"], T, iv, r["right"]) if oe.sane_iv(iv) else None
        out.setdefault(r["strike"], {"strike": r["strike"]})[r["right"]] = {
            "bid": r["bid"], "ask": r["ask"], "last": r["last"], "volume": r["volume"], "oi": r["oi"],
            "iv": None if iv is None else round(iv, 3), "delta": None if g is None else round(g["delta"], 3),
            "last_trade": r["last_trade"], "itm": (spot > r["strike"]) if r["right"] == "call" else (spot < r["strike"]),
        }
    oi_rows: dict[float, list[float]] = {}
    for r in rows:
        cell = oi_rows.setdefault(r["strike"], [0.0, 0.0])
        cell[0 if r["right"] == "call" else 1] += r["oi"]
    tuples = [(k, v[0], v[1]) for k, v in sorted(oi_rows.items())]
    return {
        "ticker": ticker, "expiry": expiry, "dte": dte, "spot": round(spot, 2), "fetched_at": _iso(ts),
        "max_pain": oe.max_pain(tuples), **oe.oi_walls(tuples, spot),
        "strikes": [out[k] for k in sorted(out)],
    }


def contract_quote(ticker: str, expiry: str, strike: float, right: str, refresh: bool = True) -> dict:
    """Fresh quote for one contract. Used when opening and evaluating paper trades."""
    rows, ts = get_chain(ticker.upper(), expiry, refresh)
    for r in rows:
        if r["right"] == right and abs(r["strike"] - strike) < 1e-6:
            bid, ask = r["bid"], r["ask"]
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else (r["last"] or None)
            return {"bid": bid, "ask": ask, "mid": mid, "last": r["last"], "iv": r["iv"], "fetched_at": _iso(ts),
                    "last_trade": r["last_trade"], "contract_symbol": r["contract_symbol"]}
    raise HTTPException(status_code=404, detail=f"No {right} {strike} contract for {ticker} {expiry}")


def spot_price(ticker: str, refresh: bool = True) -> tuple[float, str | None]:
    intra, ts = get_intraday(ticker.upper(), refresh)
    if intra["c"]:
        return intra["c"][-1], intra["t"][-1]
    daily, _ = get_daily(ticker.upper())
    return daily["c"][-1], daily["t"][-1]
