"""End-to-end journal test against a real Postgres. Skipped unless TEST_DATABASE_URL is set."""
import os
from datetime import datetime, timedelta, timezone

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    from fastapi.testclient import TestClient
    from app import db
    from app.main import app
    from app.routers import options as opt

    db.init_db()
    with db.get_connection() as c, c.cursor() as cur:
        cur.execute("TRUNCATE option_trades RESTART IDENTITY")
    state = {"spot": 100.0, "bid": 3.9, "ask": 4.1, "open": True}
    monkeypatch.setattr(opt, "spot_price", lambda tk, refresh=True: (state["spot"], None))
    monkeypatch.setattr(opt, "market_open_now", lambda now=None: state["open"])
    monkeypatch.setattr(opt, "contract_quote", lambda tk, ex, k, r, refresh=True: {
        "bid": state["bid"], "ask": state["ask"], "mid": (state["bid"] + state["ask"]) / 2, "last": 4.0, "iv": 0.3,
        "fetched_at": datetime.now(timezone.utc).isoformat(), "last_trade": None, "contract_symbol": "X"})
    return TestClient(app), state


def _open(client, **kw):
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    body = {"ticker": "NVDA", "right": "call", "strike": 100, "expiry": exp, "contracts": 2, "conviction": 72,
            "stop_underlying": 95, "target_underlying": 108, "factors": {"trend": 0.6, "news": -0.1}, **kw}
    return client.post("/api/options/trades", json=body)


def test_open_list_evaluate_close_and_stats(api):
    client, st = api
    r = _open(client)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["entry_premium"] == 4.05 and t["status"] == "open" and t["factors"]["trend"] == 0.6

    # nothing to do while price sits between stop and target
    assert client.post("/api/options/trades/evaluate").json()["actions"] == []
    live = client.get("/api/options/trades?status=open").json()["trades"][0]
    assert live["live"]["mark"] == 4.0 and live["live"]["pnl_usd"] == -10.0

    # underlying hits target -> auto close
    st["spot"], st["bid"], st["ask"] = 108.5, 9.0, 9.2
    acts = client.post("/api/options/trades/evaluate").json()["actions"]
    assert acts[0]["reason"] == "target" and acts[0]["pnl_usd"] > 0
    assert client.post(f"/api/options/trades/{t['id']}/close").status_code == 400  # already closed

    # second trade closed manually with own fill; third stopped out
    t2 = _open(client, entry_premium=5.0, source="manual", conviction=45).json()
    c2 = client.post(f"/api/options/trades/{t2['id']}/close", json={"exit_premium": 2.0}).json()
    assert c2["pnl_usd"] == -600.0 and c2["exit_reason"] == "manual"
    st.update(spot=100.0, bid=3.9, ask=4.1)
    _open(client, conviction=85)
    st.update(spot=94.0, bid=1.5, ask=1.7)
    assert client.post("/api/options/trades/evaluate").json()["actions"][0]["reason"] == "stop"

    s = client.get("/api/options/stats").json()
    assert s["n"] == 3 and s["open_positions"] == 0 and len(s["equity"]) == 3
    assert {b["bucket"] for b in s["calibration"]} == {"0-39", "40-59", "60-79", "80-100"}
    assert s["by_exit"]["target"]["n"] == 1 and "anecdotes" in s["sample_note"]


def test_guards(api):
    client, st = api
    st["open"] = False
    assert _open(client).status_code == 409                    # closed market, model fill refused
    assert _open(client, entry_premium=4.2).status_code == 200  # own fill is allowed
    st["open"] = True
    st["bid"] = 0.0
    assert _open(client).status_code == 409                    # no two-sided quote
    assert _open(client, right="spread").status_code == 400
    assert _open(client, contracts=0).status_code == 400
