import math
import random

from app import options_engine as oe


def test_put_call_parity():
    S, K, T, sig, r = 100, 105, 0.25, 0.3, oe.RISK_FREE
    c = oe.bs_price(S, K, T, sig, "call")
    p = oe.bs_price(S, K, T, sig, "put")
    assert abs((c - p) - (S - K * math.exp(-r * T))) < 1e-9


def test_delta_bounds_and_atm():
    g = oe.bs_greeks(100, 100, 0.1, 0.25, "call")
    assert 0.45 < g["delta"] < 0.6
    gp = oe.bs_greeks(100, 100, 0.1, 0.25, "put")
    assert -0.55 < gp["delta"] < -0.4
    assert g["theta"] < 0 and g["gamma"] > 0 and g["vega"] > 0


def test_iv_roundtrip():
    px = oe.bs_price(250, 260, 30 / 365, 0.42, "call")
    assert abs(oe.implied_vol(px, 250, 260, 30 / 365, "call") - 0.42) < 1e-4
    assert oe.implied_vol(0.0001, 250, 100, 30 / 365, "call") is None  # below intrinsic


def test_prob_profit_is_below_prob_itm_for_long_call():
    pop = oe.prob_profit_long(100, 100, 0.1, 0.3, "call", 3.0)
    itm = oe.prob_profit_long(100, 100, 0.1, 0.3, "call", 0.0)
    assert 0 < pop < itm < 1


def test_max_pain_and_walls():
    rows = [(90, 100, 800), (100, 500, 500), (110, 800, 100)]
    assert oe.max_pain(rows) == 100
    w = oe.oi_walls(rows, 100)
    assert w["call_wall"] == 100 or w["call_wall"] == 110
    assert w["put_wall"] == 100 or w["put_wall"] == 90
    assert oe.oi_walls([(90, 1, 800), (110, 800, 1)], 100) == {"call_wall": 110, "put_wall": 90}


def test_unusual_flow_bias():
    rows = [
        {"right": "call", "strike": 100, "expiry": "x", "volume": 5000, "oi": 400, "last": 2.0, "bid": 1.9, "ask": 2.0},
        {"right": "put", "strike": 95, "expiry": "x", "volume": 400, "oi": 20000, "last": 1.0, "bid": 0.9, "ask": 1.0},  # vol < oi, ignored
        {"right": "call", "strike": 130, "expiry": "x", "volume": 50, "oi": 1, "last": 0.1, "bid": 0.1, "ask": 0.2},  # too small
    ]
    f = oe.unusual_flow(rows)
    assert f["count"] == 1 and f["bias"] == 1.0 and f["items"][0]["side_guess"] == "buy"


def _uptrend(n=120, drift=0.004, seed=1):
    random.seed(seed)
    px, c, h, l = 100.0, [], [], []
    for _ in range(n):
        px *= 1 + drift + random.uniform(-0.006, 0.006)
        c.append(px)
        h.append(px * 1.006)
        l.append(px * 0.994)
    return c, h, l


def test_technicals_on_uptrend():
    c, h, l = _uptrend()
    t = oe.trend_score(c)
    assert t["score"] > 0.3
    assert oe.atr(h, l, c) > 0
    lv = oe.swing_levels(h, l, c[-1])
    assert all(s < c[-1] for s in lv["supports"]) and all(r > c[-1] for r in lv["resistances"])
    assert oe.rsi(c) > 55


def test_trend_score_downtrend_negative():
    c, _, _ = _uptrend(drift=-0.004)
    assert oe.trend_score(c)["score"] < -0.3


def test_hv_rank_needs_history():
    c, _, _ = _uptrend(300)
    s = oe.hv_series(c)
    assert len(s) > 200 and oe.percentile_rank(s[-1], s) is not None
    assert oe.percentile_rank(0.3, [0.2] * 10) is None
    assert oe.iv_rank(0.5, [0.3] * 5) is None
    assert oe.iv_rank(0.5, [0.2, 0.4, 0.6] * 10) == 75.0


def test_news_score_recency():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    arts = [
        {"score": 1.0, "magnitude": 0.5, "time": (now - timedelta(hours=1)).isoformat()},
        {"score": -1.0, "magnitude": 0.5, "time": (now - timedelta(hours=72)).isoformat()},
    ]
    assert oe.news_score(arts, now)["score"] > 0.7
    assert oe.news_score([], now)["available"] is False


def test_political_score_rules():
    hl = [
        {"headline": "Trump signals new export controls on advanced chips", "source": "x", "time": "t"},
        {"headline": "Local bakery wins award", "source": "x", "time": "t"},
        {"headline": "Trump announces tariff pause with China", "source": "x", "time": "t"},
    ]
    nvda = oe.political_score("NVDA", hl)
    assert len(nvda["evidence"]) == 2
    xom = oe.political_score("XOM", [{"headline": "Trump orders more drilling on federal land", "source": "x", "time": "t"}])
    assert xom["score"] > 0
    assert oe.political_score("XOM", hl)["evidence"] == []  # tariffs don't touch energy rules


def test_regime():
    c, _, _ = _uptrend()
    calm = oe.regime(c, [14.0] * 10)
    assert calm["label"] == "risk-on"
    stress = oe.regime(c, [18.0] * 5 + [32.0] * 5)
    assert stress["score"] < calm["score"]


def test_composite_and_decide():
    f = {
        "trend": {"score": 0.7, "available": True}, "flow": {"score": 0.5, "available": True},
        "news": {"score": 0.3, "available": True}, "political": {"score": 0.0, "available": True},
        "regime": {"score": 0.2, "available": True}, "insider": {"score": 0.0, "available": False},
    }
    comp = oe.composite(f)
    assert comp["score"] > 0.3 and comp["agreement"] == 1.0
    ok = {"market_open": True, "data_fresh": True, "liquidity_ok": True}
    d = oe.decide(comp, f, ok)
    assert d["direction"] == "call" and d["actionable"] and d["conviction"] > 40
    # modifiers shrink conviction
    d2 = oe.decide(comp, f, {**ok, "iv_expensive": True, "earnings_in_window": True})
    assert d2["conviction"] < d["conviction"] and len(d2["warnings"]) == 2
    # closed market blocks entry
    assert oe.decide(comp, f, {**ok, "market_open": False})["actionable"] is False
    assert oe.decide(comp, f, {**ok, "data_fresh": False})["actionable"] is False


def test_decide_no_trade_cases():
    mixed = {
        "trend": {"score": 0.5, "available": True}, "flow": {"score": -0.5, "available": True},
        "news": {"score": 0.5, "available": True}, "regime": {"score": -0.5, "available": True},
    }
    comp = oe.composite(mixed)
    assert oe.decide(comp, mixed, {})["direction"] == "no_trade"
    thin = {"trend": {"score": 0.9, "available": True}, "news": {"score": 0.9, "available": True}}
    assert "need 3" in oe.decide(oe.composite(thin), thin, {})["reasons"][0]
    no_trend = {"news": {"score": 0.9, "available": True}, "flow": {"score": 0.9, "available": True}, "regime": {"score": 0.9, "available": True}}
    assert oe.decide(oe.composite(no_trend), no_trend, {})["direction"] == "no_trade"


def test_trade_plan_call_and_put_geometry():
    p = oe.trade_plan("call", 100, 2.0, [97.0], [108.0], 6.0, rsi_v=55)
    assert p["stop"] < 100 < p["target1"] and p["stop"] <= 99.0 - 0.0
    assert p["reward_risk"] >= 1.5 and p["status"] == "enter_now"
    q = oe.trade_plan("put", 100, 2.0, [92.0], [103.0], 6.0, rsi_v=45)
    assert q["stop"] > 100 > q["target1"]
    assert q["reward_risk"] >= 1.5 and q["status"] == "enter_now"


def test_trade_plan_waits_when_rr_poor_and_gives_better_entry():
    p = oe.trade_plan("call", 100, 2.0, [95.0], [103.0], 4.0, rsi_v=55)
    assert p["status"] == "wait"
    lim = p["limit_entry"]
    assert lim is not None and p["stop"] < lim < 100
    rr_at_limit = (p["target1"] - lim) / (lim - p["stop"])
    assert abs(rr_at_limit - 1.5) < 0.05
    over = oe.trade_plan("call", 100, 2.0, [95.0], [112.0], 8.0, rsi_v=82)
    assert over["status"] == "wait" and "stretched" in over["note"]


def _chain(spot=100.0, T=30 / 365):
    rows = []
    for k in range(80, 121, 5):
        for right in ("call", "put"):
            iv = 0.30
            px = oe.bs_price(spot, k, T, iv, right)
            rows.append({"right": right, "strike": float(k), "bid": round(px * 0.97, 2), "ask": round(px * 1.03, 2),
                         "last": round(px, 2), "volume": 500, "oi": 2000, "iv": iv, "expiry": "2026-12-26"})
    return rows


def test_pick_contract_targets_delta_and_filters():
    T = 30 / 365
    plan = oe.trade_plan("call", 100, 2.0, [97.0], [108.0], 6.0, rsi_v=55)
    pick = oe.pick_contract(_chain(), 100.0, T, "call", plan)
    assert pick["found"] and 0.3 < pick["delta"] < 0.55
    assert pick["breakeven"] > pick["strike"] and 0 < pick["pop_pct"] < 60
    assert pick["est_return_at_target_pct"] > 0 > pick["est_return_at_stop_pct"]
    # junk chain: no bids
    junk = [{**r, "bid": 0.0} for r in _chain()]
    assert oe.pick_contract(junk, 100.0, T, "call")["found"] is False
    # bad Yahoo IV gets re-solved from the mid price
    fixed = [{**r, "iv": 0.00001} for r in _chain()]
    assert oe.pick_contract(fixed, 100.0, T, "put")["found"] is True


def test_staleness():
    assert oe.staleness(60, True)["fresh"] is True
    assert oe.staleness(3600, True)["label"] == "stale"
    assert oe.staleness(60, False)["label"] == "market closed"
    assert oe.staleness(None, True)["fresh"] is False
