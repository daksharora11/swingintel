"""
Options Desk engine: pure math and decision logic, no network, no database.

Everything here takes plain lists/dicts and returns plain dicts, so it can be
unit tested with synthetic data (see backend/tests). The router in
app/routers/options.py is the only place that talks to yfinance / news APIs.

What this module does NOT claim:
  * "Conviction" is a model score from 0 to 100, not a probability of winning.
    Whether it means anything is decided by the calibration table in the
    paper-trade stats, once enough trades exist.
  * The contract "PoP" is a Black-Scholes risk-neutral estimate. It ignores
    fat tails, skew and event risk.
  * Trade side (buy vs sell) of unusual flow is a guess from where the last
    trade printed relative to bid/ask. Public free data has no true tape.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

RISK_FREE = 0.045  # flat assumption, good enough for 2 to 8 week options

# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1d2(S: float, K: float, T: float, r: float, sigma: float):
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vt
    return d1, d1 - vt


def bs_price(S: float, K: float, T: float, sigma: float, right: str, r: float = RISK_FREE) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if right == "call" else max(0.0, K - S)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    if right == "call":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_greeks(S: float, K: float, T: float, sigma: float, right: str, r: float = RISK_FREE) -> dict:
    """delta, gamma, theta (per calendar day, in dollars per share), vega (per 1 vol point)."""
    if T <= 0 or sigma <= 0:
        itm = (S > K) if right == "call" else (S < K)
        return {"delta": (1.0 if right == "call" else -1.0) if itm else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = _d1d2(S, K, T, r, sigma)
    gamma = _npdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * _npdf(d1) * math.sqrt(T) / 100.0
    first = -S * _npdf(d1) * sigma / (2.0 * math.sqrt(T))
    if right == "call":
        delta = _ncdf(d1)
        theta = (first - r * K * math.exp(-r * T) * _ncdf(d2)) / 365.0
    else:
        delta = _ncdf(d1) - 1.0
        theta = (first + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def implied_vol(price: float, S: float, K: float, T: float, right: str, r: float = RISK_FREE) -> float | None:
    """Bisection IV solver. Returns None when the price cannot be explained by any vol in [1%, 500%]."""
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    lo, hi = 0.01, 5.0
    if bs_price(S, K, T, lo, right, r) > price or bs_price(S, K, T, hi, right, r) < price:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, right, r) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def sane_iv(iv: float | None) -> bool:
    # Yahoo reports 0.00001 style junk for illiquid or after-hours quotes.
    return iv is not None and 0.05 <= iv <= 4.0


def prob_profit_long(S: float, K: float, T: float, sigma: float, right: str, premium: float, r: float = RISK_FREE) -> float:
    """Risk-neutral probability that a long option finishes beyond its breakeven at expiry."""
    if T <= 0 or sigma <= 0:
        return 0.0
    be = K + premium if right == "call" else K - premium
    if be <= 0:
        return 1.0
    _, d2 = _d1d2(S, be, T, r, sigma)
    return _ncdf(d2) if right == "call" else _ncdf(-d2)


# ---------------------------------------------------------------------------
# Option chain structure
# ---------------------------------------------------------------------------


def max_pain(rows: list[tuple[float, float, float]]) -> float | None:
    """rows: (strike, call_open_interest, put_open_interest). Strike where option holders lose the most."""
    rows = [r for r in rows if r[1] or r[2]]
    if not rows:
        return None
    best_strike, best_pay = None, None
    for s, _, _ in rows:
        pay = sum(c * max(0.0, s - k) + p * max(0.0, k - s) for k, c, p in rows)
        if best_pay is None or pay < best_pay:
            best_strike, best_pay = s, pay
    return best_strike


def oi_walls(rows: list[tuple[float, float, float]], spot: float) -> dict:
    """Largest call OI at or above spot (resistance-like) and largest put OI at or below spot (support-like)."""
    calls = [(c, k) for k, c, _ in rows if k >= spot and c > 0]
    puts = [(p, k) for k, _, p in rows if k <= spot and p > 0]
    return {
        "call_wall": max(calls)[1] if calls else None,
        "put_wall": max(puts)[1] if puts else None,
    }


def put_call_ratios(rows: list[dict]) -> dict:
    cv = sum(r["volume"] or 0 for r in rows if r["right"] == "call")
    pv = sum(r["volume"] or 0 for r in rows if r["right"] == "put")
    coi = sum(r["oi"] or 0 for r in rows if r["right"] == "call")
    poi = sum(r["oi"] or 0 for r in rows if r["right"] == "put")
    return {
        "volume": round(pv / cv, 2) if cv else None,
        "open_interest": round(poi / coi, 2) if coi else None,
        "call_volume": cv,
        "put_volume": pv,
    }


def unusual_flow(rows: list[dict], min_volume: int = 300, min_notional: float = 25_000.0, min_vol_oi: float = 1.0, top: int = 8) -> dict:
    """
    rows: dicts with right, strike, expiry, volume, oi, last, bid, ask.
    Flags contracts trading far above their open interest. Side is a guess
    from where the last print sits against the quote.
    """
    flagged = []
    for r in rows:
        vol = r.get("volume") or 0
        if vol < min_volume:
            continue
        oi = r.get("oi") or 0
        ratio = vol / oi if oi else float("inf")
        if ratio < min_vol_oi:
            continue
        bid, ask, last = r.get("bid") or 0.0, r.get("ask") or 0.0, r.get("last") or 0.0
        px = (bid + ask) / 2 if bid and ask else last
        notional = vol * px * 100
        if notional < min_notional:
            continue
        side = "unknown"
        if bid and ask and last:
            if last >= ask * 0.98:
                side = "buy"
            elif last <= bid * 1.02:
                side = "sell"
        flagged.append({
            "right": r["right"], "strike": r["strike"], "expiry": r.get("expiry"),
            "volume": vol, "oi": oi, "vol_oi": None if ratio == float("inf") else round(ratio, 1),
            "notional": round(notional), "side_guess": side, "last": last,
        })
    flagged.sort(key=lambda x: x["notional"], reverse=True)

    bull = bear = 0.0
    for f in flagged:
        sign = 0
        if f["side_guess"] == "buy":
            sign = 1 if f["right"] == "call" else -1
        elif f["side_guess"] == "sell":
            sign = -1 if f["right"] == "call" else 1
        if sign > 0:
            bull += f["notional"]
        elif sign < 0:
            bear += f["notional"]
    total = bull + bear
    bias = (bull - bear) / total if total else 0.0
    return {"items": flagged[:top], "count": len(flagged), "bias": round(bias, 2), "directional_notional": round(total)}


# ---------------------------------------------------------------------------
# Technicals on the underlying
# ---------------------------------------------------------------------------


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):  # Wilder smoothing
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, len(closes))]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def swing_levels(highs: list[float], lows: list[float], spot: float, lookback: int = 90, wing: int = 3) -> dict:
    """Pivot highs/lows over the lookback. Returns nearest supports (below spot, nearest first) and resistances."""
    h, l = highs[-lookback:], lows[-lookback:]
    sup, res = [], []
    for i in range(wing, len(h) - wing):
        if l[i] == min(l[i - wing:i + wing + 1]):
            sup.append(l[i])
        if h[i] == max(h[i - wing:i + wing + 1]):
            res.append(h[i])
    sup = sorted({round(x, 2) for x in sup if x < spot}, reverse=True)
    res = sorted({round(x, 2) for x in res if x > spot})
    if not sup and l:
        sup = [round(min(l[-20:]), 2)] if min(l[-20:]) < spot else []
    if not res and h:
        res = [round(max(h[-20:]), 2)] if max(h[-20:]) > spot else []
    return {"supports": sup[:3], "resistances": res[:3]}


def hv_series(closes: list[float], window: int = 20) -> list[float]:
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    out = []
    for i in range(window, len(rets) + 1):
        w = rets[i - window:i]
        m = sum(w) / window
        var = sum((x - m) ** 2 for x in w) / (window - 1)
        out.append(math.sqrt(var) * math.sqrt(252))
    return out


def percentile_rank(value: float, series: list[float]) -> float | None:
    if len(series) < 30:
        return None
    return round(100.0 * sum(1 for x in series if x <= value) / len(series), 1)


def iv_rank(current: float, history: list[float], min_samples: int = 20) -> float | None:
    """True IV rank from our own stored daily ATM IV. None until enough days are logged."""
    if len(history) < min_samples:
        return None
    lo, hi = min(history), max(history)
    if hi - lo < 1e-9:
        return None
    return round(100.0 * (current - lo) / (hi - lo), 1)


def trend_score(closes: list[float]) -> dict:
    """Directional score in [-1, 1] from trend, momentum and a small RSI tilt."""
    c = closes[-1]
    s20, s50, r = sma(closes, 20), sma(closes, 50), rsi(closes)
    if s20 is None:
        return {"score": 0.0, "available": False, "detail": "Not enough price history"}
    score = 0.35 if c > s20 else -0.35
    if s50 is not None:
        score += 0.25 if s20 > s50 else -0.25
    if len(closes) > 6:
        mom = c / closes[-6] - 1.0
        score += max(-1.0, min(1.0, mom / 0.05)) * 0.25
    if r is not None:
        if r > 70:
            score -= 0.15
        elif r < 30:
            score += 0.15
        else:
            score += (r - 50) / 50 * 0.15
    score = max(-1.0, min(1.0, score))
    return {
        "score": round(score, 3), "available": True, "rsi": None if r is None else round(r, 1),
        "sma20": None if s20 is None else round(s20, 2), "sma50": None if s50 is None else round(s50, 2),
        "detail": f"Price {'above' if c > s20 else 'below'} 20d avg" + (f", 20d {'above' if s20 > s50 else 'below'} 50d" if s50 else ""),
    }


# ---------------------------------------------------------------------------
# News, policy and regime factors
# ---------------------------------------------------------------------------


def news_score(articles: list[dict], now: datetime | None = None, half_life_h: float = 24.0) -> dict:
    """articles: {score in [-1,1], magnitude in [0,1], time ISO string}. Recency-weighted mean."""
    now = now or datetime.now(timezone.utc)
    num = den = 0.0
    used = 0
    newest = None
    for a in articles:
        try:
            ts = datetime.fromisoformat(str(a.get("time")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        age_h = max(0.0, (now - ts).total_seconds() / 3600.0)
        w = (a.get("magnitude") or 0.15) * 0.5 ** (age_h / half_life_h)
        num += (a.get("score") or 0.0) * w
        den += w
        used += 1
        newest = ts if newest is None or ts > newest else newest
    if not used or den == 0:
        return {"score": 0.0, "available": False, "detail": "No recent headlines"}
    return {
        "score": round(max(-1.0, min(1.0, num / den)), 3), "available": True,
        "detail": f"{used} headlines, newest {newest.isoformat() if newest else 'n/a'}",
        "as_of": newest.isoformat() if newest else None,
    }


# Hand-written sector map and policy rules. This is a transparent keyword
# system, not a model. Edit freely; every match is returned as evidence.
SECTOR = {
    "NVDA": "semis", "AMD": "semis", "TSLA": "autos", "AAPL": "hardware", "MSFT": "software",
    "GOOGL": "software", "AMZN": "retail", "META": "software", "NFLX": "media", "DIS": "media",
    "XOM": "energy", "JPM": "banks", "PFE": "pharma", "WMT": "retail", "BA": "aerospace",
    "SPY": "broad", "QQQ": "broad", "IWM": "broad",
}

_RELIEF = [("tariff pause", 1.0), ("tariff relief", 1.0), ("trade deal", 0.8), ("trade truce", 0.8), ("tariffs delayed", 0.8), ("cuts tariffs", 0.8), ("lifts tariffs", 0.8)]
_TARIFF_HURT = {"semis", "hardware", "autos", "retail", "aerospace", "broad", "media"}
POLICY_RULES: dict[str, list[tuple[str, float]]] = {
    "semis": [("export control", -1.0), ("chip ban", -1.0), ("china ban", -0.8), ("chips act", 0.6), ("subsidy", 0.4), ("ai executive order", 0.4)],
    "hardware": [("iphone tariff", -1.0), ("china", -0.2)],
    "autos": [("auto tariff", -1.0), ("ev credit", -0.6), ("ev tax", -0.6), ("emissions rule", 0.3)],
    "energy": [("drill", 0.7), ("oil sanctions", 0.5), ("lng", 0.4), ("energy emergency", 0.6), ("opec", 0.0), ("ban drilling", -0.8)],
    "banks": [("deregulation", 0.7), ("capital rules", 0.5), ("rate cut", 0.3), ("credit card cap", -0.8), ("fed pressure", -0.2)],
    "pharma": [("drug pricing", -0.8), ("most favored nation", -0.9), ("pharma tariff", -0.8), ("medicare negotiation", -0.6), ("fda approval", 0.3)],
    "aerospace": [("defense budget", 0.6), ("faa", -0.3), ("boeing", -0.1)],
    "software": [("antitrust", -0.7), ("section 230", -0.4), ("ai executive order", 0.3), ("breakup", -0.7)],
    "media": [("fcc", -0.2), ("merger approval", 0.4)],
    "retail": [("consumer", 0.0)],
    "broad": [("rate cut", 0.4), ("shutdown", -0.5), ("debt ceiling", -0.4)],
}
_POLITICAL_ACTORS = ("trump", "white house", "administration", "president", "treasury", "commerce department", "congress", "senate")


def political_score(ticker: str, headlines: list[dict]) -> dict:
    """headlines: {headline, source, time}. Only headlines that mention a political actor are considered."""
    sector = SECTOR.get(ticker.upper(), "broad")
    rules = list(POLICY_RULES.get(sector, []))
    evidence, total = [], 0.0
    for h in headlines:
        text = (h.get("headline") or "").lower()
        if not text or not any(a in text for a in _POLITICAL_ACTORS):
            continue
        hit = None
        remaining = text
        for kw, eff in _RELIEF:
            if kw in remaining:
                if sector in _TARIFF_HURT:
                    hit = (kw, eff)
                remaining = remaining.replace(kw, " ")
        if hit is None:
            for kw, eff in sorted(rules, key=lambda x: -len(x[0])):
                if kw in remaining and eff != 0.0:
                    hit = (kw, eff)
                    break
        if hit is None and "tariff" in remaining and sector in _TARIFF_HURT:
            hit = ("tariff", -0.6)
        if hit:
            total += hit[1]
            evidence.append({"headline": h.get("headline"), "source": h.get("source"), "time": h.get("time"), "matched": hit[0], "effect": hit[1]})
    if not evidence:
        return {"score": 0.0, "available": True, "detail": f"No policy headlines touching {sector}", "evidence": [], "sector": sector}
    return {
        "score": round(max(-1.0, min(1.0, total / 3.0)), 3), "available": True,
        "detail": f"{len(evidence)} policy headline(s) touching {sector}", "evidence": evidence[:6], "sector": sector,
    }


def regime(spy_closes: list[float], vix_closes: list[float]) -> dict:
    t = trend_score(spy_closes) if len(spy_closes) >= 20 else None
    vix = vix_closes[-1] if vix_closes else None
    vix_chg5 = (vix_closes[-1] / vix_closes[-6] - 1.0) if len(vix_closes) > 6 else None
    if t is None or vix is None:
        return {"score": 0.0, "available": False, "label": "unknown", "detail": "Missing SPY or VIX data"}
    score = t["score"]
    if vix > 30:
        score -= 0.4
    elif vix > 25:
        score -= 0.25
    elif vix > 20:
        score -= 0.1
    elif vix < 15:
        score += 0.05
    if vix_chg5 is not None and vix_chg5 > 0.2:
        score -= 0.15
    score = max(-1.0, min(1.0, score))
    label = "risk-on" if score > 0.25 else ("risk-off" if score < -0.25 else "mixed")
    return {
        "score": round(score, 3), "available": True, "label": label, "vix": round(vix, 2),
        "vix_5d_change_pct": None if vix_chg5 is None else round(vix_chg5 * 100, 1),
        "spy_trend": t["score"], "detail": f"{label}, VIX {vix:.1f}",
    }


# ---------------------------------------------------------------------------
# Composite signal
# ---------------------------------------------------------------------------

WEIGHTS = {"trend": 0.30, "flow": 0.20, "news": 0.15, "political": 0.10, "insider": 0.05, "regime": 0.10}
ACT_THRESHOLD = 0.20
MIN_FACTORS = 3
MIN_AGREEMENT = 0.60


def composite(factors: dict[str, dict]) -> dict:
    """factors: name -> {score, available}. Weights renormalise over the factors that have data."""
    avail = {k: f for k, f in factors.items() if f.get("available") and k in WEIGHTS}
    wsum = sum(WEIGHTS[k] for k in avail)
    if not avail or wsum == 0:
        return {"score": 0.0, "agreement": 0.0, "n_factors": 0, "contributions": {}}
    score = sum(WEIGHTS[k] * avail[k]["score"] for k in avail) / wsum
    live = [k for k in avail if abs(avail[k]["score"]) >= 0.15]
    sign = 1 if score >= 0 else -1
    agree = sum(1 for k in live if avail[k]["score"] * sign > 0)
    agreement = agree / len(live) if live else 0.0
    return {
        "score": round(score, 3), "agreement": round(agreement, 2), "n_factors": len(avail),
        "contributions": {k: round(WEIGHTS[k] / wsum * avail[k]["score"], 3) for k in avail},
    }


def decide(comp: dict, factors: dict[str, dict], ctx: dict) -> dict:
    """
    ctx keys: iv_expensive (bool), earnings_in_window (bool), liquidity_ok (bool),
              market_open (bool), data_fresh (bool)
    Returns direction in {call, put, no_trade}, conviction 0..100, and human-readable reasons.
    """
    reasons, warnings = [], []
    direction = "no_trade"
    if not factors.get("trend", {}).get("available"):
        reasons.append("No price trend data")
    elif comp["n_factors"] < MIN_FACTORS:
        reasons.append(f"Only {comp['n_factors']} factor(s) have data, need {MIN_FACTORS}")
    elif abs(comp["score"]) < ACT_THRESHOLD:
        reasons.append(f"Composite {comp['score']:+.2f} is inside the no-trade band (±{ACT_THRESHOLD})")
    elif comp["agreement"] < MIN_AGREEMENT:
        reasons.append(f"Factors disagree (only {int(comp['agreement'] * 100)}% point the same way)")
    else:
        direction = "call" if comp["score"] > 0 else "put"

    conviction = 0.0
    if direction != "no_trade":
        conviction = min(1.0, abs(comp["score"]) / 0.6) * (0.5 + 0.5 * comp["agreement"])
        if ctx.get("iv_expensive"):
            conviction *= 0.85
            warnings.append("Options are expensive versus their own history, so long premium needs a bigger move to pay")
        if ctx.get("earnings_in_window"):
            conviction *= 0.75
            warnings.append("Earnings fall inside the expiry window. IV crush can lose money even when direction is right")
        if not ctx.get("liquidity_ok", True):
            conviction *= 0.8
            warnings.append("Option liquidity is thin, so expect poor fills")
    actionable = direction != "no_trade"
    if actionable and not ctx.get("market_open", True):
        actionable = False
        warnings.append("Market is closed. Showing a preview built on the last close")
    if actionable and not ctx.get("data_fresh", True):
        actionable = False
        warnings.append("Quote data is stale, so entries are blocked until it refreshes")
    return {
        "direction": direction, "conviction": round(conviction * 100), "actionable": actionable,
        "reasons": reasons, "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Entry / stop / target plan on the underlying
# ---------------------------------------------------------------------------


def trade_plan(direction: str, spot: float, atr_v: float, supports: list[float], resistances: list[float],
               expected_move: float | None, rsi_v: float | None = None, min_rr: float = 1.5) -> dict:
    call = direction == "call"
    if call:
        sup = supports[0] if supports else None
        stop = (sup - 0.25 * atr_v) if sup is not None and (spot - sup) <= 2.5 * atr_v else spot - 1.5 * atr_v
        stop = min(stop, spot - 0.75 * atr_v)
        res = resistances[0] if resistances else None
        t1 = res if res is not None and (res - spot) >= atr_v else spot + 1.5 * atr_v
        t2 = max(t1, spot + (expected_move or 0.0))
        overextended = rsi_v is not None and rsi_v > 75
    else:
        res = resistances[0] if resistances else None
        stop = (res + 0.25 * atr_v) if res is not None and (res - spot) <= 2.5 * atr_v else spot + 1.5 * atr_v
        stop = max(stop, spot + 0.75 * atr_v)
        sup = supports[0] if supports else None
        t1 = sup if sup is not None and (spot - sup) >= atr_v else spot - 1.5 * atr_v
        t2 = min(t1, spot - (expected_move or 0.0))
        overextended = rsi_v is not None and rsi_v < 25
    risk, reward = abs(spot - stop), abs(t1 - spot)
    rr = reward / risk if risk else 0.0
    ideal = (t1 + min_rr * stop) / (1 + min_rr)  # entry where reward/risk equals min_rr
    ideal_ok = (ideal < spot and ideal > stop) if call else (ideal > spot and ideal < stop)

    if rr >= min_rr and not overextended:
        status = "enter_now"
        zone = (spot - 0.25 * atr_v, spot) if call else (spot, spot + 0.25 * atr_v)
        limit = None
        note = f"Reward/risk {rr:.1f} meets the {min_rr} minimum"
    else:
        status = "wait"
        if overextended:
            limit = spot - 0.5 * atr_v if call else spot + 0.5 * atr_v
            if ideal_ok:
                limit = min(limit, ideal) if call else max(limit, ideal)
            note = "Price is stretched (RSI extreme). Wait for a pullback"
        else:
            limit = ideal if ideal_ok else None
            note = f"Reward/risk is only {rr:.1f} from here"
            if limit is None:
                note += ", and no sensible pullback entry exists"
        zone = (limit - 0.15 * atr_v, limit + 0.15 * atr_v) if limit is not None else None
    return {
        "status": status, "entry_zone": None if zone is None else [round(zone[0], 2), round(zone[1], 2)],
        "limit_entry": None if limit is None else round(limit, 2),
        "stop": round(stop, 2), "target1": round(t1, 2), "target2": round(t2, 2),
        "risk_per_share": round(risk, 2), "reward_per_share": round(reward, 2), "reward_risk": round(rr, 2), "note": note,
    }


# ---------------------------------------------------------------------------
# Contract picker
# ---------------------------------------------------------------------------


def pick_contract(rows: list[dict], spot: float, T: float, direction: str, plan: dict | None = None,
                  target_delta: float = 0.40, max_spread_pct: float = 0.15, min_oi: int = 100) -> dict:
    """
    rows: dicts with right, strike, bid, ask, last, volume, oi, iv, expiry for ONE expiry.
    Chooses the liquid contract whose |delta| is closest to target_delta.
    """
    best, best_gap = None, None
    skipped = {"no_quote": 0, "wide_spread": 0, "illiquid": 0, "bad_iv": 0}
    for r in rows:
        if r["right"] != direction:
            continue
        bid, ask = r.get("bid") or 0.0, r.get("ask") or 0.0
        if bid <= 0 or ask <= 0 or ask < bid:
            skipped["no_quote"] += 1
            continue
        mid = (bid + ask) / 2
        spread = ask - bid
        if spread / mid > max_spread_pct and spread > 0.05:
            skipped["wide_spread"] += 1
            continue
        if (r.get("oi") or 0) < min_oi and (r.get("volume") or 0) < 100:
            skipped["illiquid"] += 1
            continue
        iv = r.get("iv")
        if not sane_iv(iv):
            iv = implied_vol(mid, spot, r["strike"], T, direction)
        if not sane_iv(iv):
            skipped["bad_iv"] += 1
            continue
        g = bs_greeks(spot, r["strike"], T, iv, direction)
        gap = abs(abs(g["delta"]) - target_delta)
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, (r, mid, spread, iv, g)
    if best is None:
        return {"found": False, "skipped": skipped, "reason": "No contract passed the liquidity and spread filters"}
    r, mid, spread, iv, g = best
    fill = mid + 0.25 * spread  # assumed paper fill: a quarter of the spread paid
    K = r["strike"]
    out = {
        "found": True, "right": direction, "strike": K, "expiry": r.get("expiry"),
        "bid": r["bid"], "ask": r["ask"], "mid": round(mid, 2), "assumed_fill": round(fill, 2),
        "spread_pct": round(spread / mid * 100, 1), "iv": round(iv, 3), "volume": r.get("volume"), "oi": r.get("oi"),
        "delta": round(g["delta"], 3), "gamma": round(g["gamma"], 4), "theta_per_day": round(g["theta"], 3), "vega_per_vol_pt": round(g["vega"], 3),
        "breakeven": round(K + fill if direction == "call" else K - fill, 2),
        "cost_per_contract": round(fill * 100, 2),
        "pop_pct": round(prob_profit_long(spot, K, T, iv, direction, fill) * 100, 1),
        "contract_symbol": r.get("contract_symbol"), "skipped": skipped,
    }
    if plan:
        hold = min(5 / 365.0, T / 2)  # assume about a week to play out, as a rough horizon
        T_after = max(T - hold, 1 / 365.0)
        p_t = bs_price(plan["target1"], K, T_after, iv, direction)
        p_s = bs_price(plan["stop"], K, T_after, iv, direction)
        out["est_premium_at_target"] = round(p_t, 2)
        out["est_premium_at_stop"] = round(p_s, 2)
        out["est_return_at_target_pct"] = round((p_t / fill - 1) * 100, 1)
        out["est_return_at_stop_pct"] = round((p_s / fill - 1) * 100, 1)
    return out


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def staleness(age_seconds: float | None, market_open: bool, fresh_limit_s: int = 20 * 60) -> dict:
    if age_seconds is None:
        return {"age_s": None, "fresh": False, "label": "unknown"}
    if not market_open:
        return {"age_s": int(age_seconds), "fresh": False, "label": "market closed"}
    return {"age_s": int(age_seconds), "fresh": age_seconds <= fresh_limit_s, "label": "fresh" if age_seconds <= fresh_limit_s else "stale"}
