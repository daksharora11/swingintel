import random
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import options_engine as oe
from app.main import app
from app.routers import options as opt
from app.routers import options_trades as ot

client = TestClient(app)
NOW = datetime.now(timezone.utc)


def _bars(n=260, drift=0.0035, seed=3, last_age_s=30):
    random.seed(seed)
    px, o, h, l, c, v, t = 100.0, [], [], [], [], [], []
    start = NOW - timedelta(days=n)
    for i in range(n):
        px *= 1 + drift + random.uniform(-0.007, 0.007)
        o.append(px); c.append(px); h.append(px * 1.007); l.append(px * 0.993); v.append(1e6)
        t.append((start + timedelta(days=i)).isoformat())
    t[-1] = (NOW - timedelta(seconds=last_age_s)).isoformat()
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _chain_rows(spot, expiry, dte):
    T = max(dte, 1) / 365
    rows = []
    step = 5
    for k in range(int(spot * 0.7 // step * step), int(spot * 1.3), step):
        for right in ("call", "put"):
            px = oe.bs_price(spot, k, T, 0.32, right)
            if px < 0.05:
                continue
            hot = right == "call" and k > spot
            rows.append({"right": right, "strike": float(k), "expiry": expiry, "bid": round(px * 0.98, 2), "ask": round(px * 1.02, 2),
                         "last": round(px * 1.02, 2) if hot else round(px, 2), "volume": 3000 if hot else 120, "oi": 1500, "iv": 0.32,
                         "last_trade": (NOW - timedelta(minutes=14)).isoformat(), "contract_symbol": f"X{k}{right[0]}"})
    return rows


@pytest.fixture
def patched(monkeypatch):
    state = {"open": True, "age": 30, "news": "bull"}
    daily = _bars()
    today = NOW.astimezone(opt.ET_TZ).date()
    exps = [(today + timedelta(days=d)).isoformat() for d in (7, 30, 58)]

    def get_daily(tk, refresh=False, period="1y"):
        return (_bars(seed=9, drift=0.001) if tk in ("SPY", "^VIX") and tk == "SPY" else
                ({"c": [14.0] * 40, "t": [], "h": [], "l": [], "o": [], "v": []} if tk == "^VIX" else daily)), NOW.timestamp()

    def get_intraday(tk, refresh=False):
        spot = daily["c"][-1]
        n = 5
        ts = [(NOW - timedelta(seconds=state["age"] + 60 * (n - 1 - i))).isoformat() for i in range(n)]
        return {"t": ts, "o": [spot] * n, "h": [spot * 1.001] * n, "l": [spot * 0.999] * n, "c": [spot] * n, "v": [1e4] * n}, NOW.timestamp()

    def get_chain(tk, expiry, refresh=False):
        dte = (date.fromisoformat(expiry) - today).days
        return _chain_rows(daily["c"][-1], expiry, dte), NOW.timestamp()

    def headlines(tk, refresh=False):
        if state["news"] == "fail":
            return {"source": None, "items": []}, NOW.timestamp()
        txt = "beats estimates and raises guidance, shares surge" if state["news"] == "bull" else "misses estimates, warns on outlook"
        return {"source": "test", "items": [{"headline": f"{tk} {txt}", "source": "t", "time": (NOW - timedelta(hours=2)).isoformat()}] * 3}, NOW.timestamp()

    monkeypatch.setattr(opt, "get_daily", get_daily)
    monkeypatch.setattr(opt, "get_intraday", get_intraday)
    monkeypatch.setattr(opt, "get_expirations", lambda tk, refresh=False: (exps, NOW.timestamp()))
    monkeypatch.setattr(opt, "get_chain", get_chain)
    monkeypatch.setattr(opt, "ticker_headlines", headlines)
    monkeypatch.setattr(opt, "political_headlines", lambda refresh=False: ({"sources": ["test"], "items": [{"headline": "Trump announces tariff pause with China", "source": "t", "time": NOW.isoformat()}]}, NOW.timestamp()))
    monkeypatch.setattr(opt, "insider_factor", lambda tk: {"score": 0.5, "available": True, "detail": "x"})
    monkeypatch.setattr(opt, "get_earnings_date", lambda tk: None)
    monkeypatch.setattr(opt, "market_open_now", lambda now=None: state["open"])
    monkeypatch.setattr(opt, "_iv_history", lambda tk: [])
    monkeypatch.setattr(opt, "_log_iv", lambda tk, iv: None)
    return state


def test_bullish_signal_end_to_end(patched):
    r = client.get("/api/options/NVDA/signal")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["direction"] == "call" and d["actionable"] is True
    assert d["plan"]["stop"] < d["spot"] < d["plan"]["target1"]
    c = d["contract"]
    assert c["found"] and c["right"] == "call" and 0.25 < c["delta"] < 0.55 and c["cost_per_contract"] > 0
    assert 14 <= d["expiry"]["dte"] <= 45
    assert d["meta"]["quote"]["fresh"] is True
    assert d["factors"]["political"]["score"] > 0  # tariff pause is good for semis
    assert d["volatility"]["expected_move"] > 0 and d["levels"]["max_pain"] is not None
    assert d["flow"]["count"] >= 1 and d["flow"]["bias"] > 0 and d["factors"]["flow"]["available"]


def test_market_closed_blocks_entry_but_still_shows_preview(patched):
    patched["open"] = False
    d = client.get("/api/options/NVDA/signal").json()
    assert d["direction"] == "call" and d["actionable"] is False
    assert any("closed" in w.lower() for w in d["warnings"])


def test_stale_quote_blocks_entry(patched):
    patched["age"] = 3 * 3600
    d = client.get("/api/options/NVDA/signal").json()
    assert d["meta"]["quote"]["label"] == "stale" and d["actionable"] is False


def test_missing_news_degrades_gracefully(patched):
    patched["news"] = "fail"
    d = client.get("/api/options/NVDA/signal").json()
    assert d["factors"]["news"]["available"] is False
    assert d["direction"] in ("call", "no_trade")


def test_bearish_news_pulls_against_uptrend(patched):
    bull = client.get("/api/options/NVDA/signal").json()["composite"]["score"]
    patched["news"] = "bear"
    bear = client.get("/api/options/NVDA/signal").json()["composite"]["score"]
    assert bear < bull


def test_chain_endpoint(patched):
    exp = client.get("/api/options/NVDA/signal").json()["expiry"]["date"]
    r = client.get(f"/api/options/NVDA/chain?expiry={exp}&width=5")
    assert r.status_code == 200
    d = r.json()
    assert len(d["strikes"]) == 11 and d["strikes"][5]["call"]["delta"] is not None
    assert d["max_pain"] and d["call_wall"] and d["put_wall"]


def test_trades_endpoints_503_without_db(patched):
    assert client.get("/api/options/trades").status_code == 503
    assert client.get("/api/options/stats").status_code == 503


# ----- pure trade logic -----
TODAY = date(2026, 11, 23)


def _trade(**kw):
    base = {"right": "call", "strike": 100.0, "expiry": date(2026, 12, 18), "entry_premium": 4.0, "stop_underlying": 95.0, "target_underlying": 110.0}
    return {**base, **kw}


def test_exit_rules():
    assert ot.evaluate_exit(_trade(), 94.9, 2.9, 3.1, TODAY, True)["reason"] == "stop"
    assert ot.evaluate_exit(_trade(), 110.5, 8.0, 8.2, TODAY, True)["reason"] == "target"
    assert ot.evaluate_exit(_trade(), 99.0, 1.9, 2.0, TODAY, True)["reason"] == "premium_stop"
    assert ot.evaluate_exit(_trade(expiry=date(2026, 11, 25)), 101.0, 3.9, 4.1, TODAY, True)["reason"] == "time_exit"
    assert ot.evaluate_exit(_trade(), 101.0, 4.4, 4.6, TODAY, True) is None
    assert ot.evaluate_exit(_trade(), 94.0, 1, 1.1, TODAY, False) is None  # market closed: no stop fills
    exp = ot.evaluate_exit(_trade(expiry=date(2026, 11, 20)), 103.0, None, None, TODAY, False)
    assert exp == {"reason": "expired", "exit_premium": 3.0}
    put = _trade(right="put", stop_underlying=105.0, target_underlying=90.0)
    assert ot.evaluate_exit(put, 105.1, 3, 3.2, TODAY, True)["reason"] == "stop"
    assert ot.evaluate_exit(put, 89.0, 9, 9.2, TODAY, True)["reason"] == "target"


def test_fills_and_pnl():
    assert ot.entry_fill(3.9, 4.1) == 4.05 and ot.exit_fill(3.9, 4.1) == 3.95
    assert ot.pnl(4.0, 6.0, 2) == (400.0, 50.0)
    assert ot.pnl(4.0, 1.0, 1) == (-300.0, -75.0)


def test_stats_calibration_and_factors():
    def mk(right, conv, pnl_pct, f, i):
        usd = pnl_pct
        return {"right": right, "conviction": conv, "pnl_pct": pnl_pct, "pnl_usd": usd, "exit_reason": "target" if usd > 0 else "stop",
                "source": "model", "factors": f, "exit_at": datetime(2026, 11, 1 + i, tzinfo=timezone.utc)}
    rows = [mk("call", 85, 40, {"trend": 0.7}, 0), mk("call", 82, 30, {"trend": 0.6}, 1), mk("call", 45, -50, {"trend": -0.3}, 2),
            mk("put", 62, -50, {"trend": 0.4}, 3), mk("put", 65, 20, {"trend": -0.5}, 4)]
    s = ot.compute_stats(rows)
    assert s["n"] == 5 and s["win_rate"] == 60.0 and "anecdotes" in s["sample_note"]
    cal = {b["bucket"]: b for b in s["calibration"]}
    assert cal["80-100"]["win_rate"] == 100.0 and cal["40-59"]["win_rate"] == 0.0 and cal["0-39"]["n"] == 0
    tr = s["factors"]["trend"]
    assert tr["aligned"]["n"] == 3 and tr["aligned"]["win_rate"] == 100.0 and tr["not_aligned"]["n"] == 2
    assert s["equity"][-1]["pnl"] == -10.0 and s["profit_factor"] == round(90 / 100, 2)
    assert ot.compute_stats([])["n"] == 0


def test_tradier_parsers_handle_single_and_list(monkeypatch):
    from app.routers import options as o
    monkeypatch.setenv("TRADIER_TOKEN", "x")

    def fake(path, params):
        if "expirations" in path:
            return {"expirations": {"date": "2026-12-18"}}  # single result comes back as a dict value
        return {"options": {"option": {"symbol": "AAPL261218C00200000", "option_type": "call", "strike": 200,
                                        "bid": 1.0, "ask": 1.2, "last": 1.1, "volume": 10, "open_interest": 50,
                                        "expiration_date": "2026-12-18", "greeks": {"mid_iv": 0.3}}}}
    monkeypatch.setattr(o, "_tradier_get", fake)
    assert o._tradier_expirations("AAPL") == ["2026-12-18"]
    rows = o._tradier_chain("AAPL", "2026-12-18")
    assert len(rows) == 1 and rows[0]["right"] == "call" and rows[0]["iv"] == 0.3 and rows[0]["oi"] == 50


def test_expirations_report_error_when_all_providers_fail(monkeypatch):
    from app.routers import options as o
    monkeypatch.delenv("TRADIER_TOKEN", raising=False)
    monkeypatch.setattr(o, "_yahoo_expirations", lambda t: (_ for _ in ()).throw(RuntimeError("blocked")))
    o._cache.clear()
    exps, _ = o.get_expirations("ZZZZ", refresh=True)
    assert exps == [] and "blocked" in o._chain_errors["ZZZZ"]
