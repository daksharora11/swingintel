"""
Options paper-trade journal.

Records a trade idea (from the Options Desk or typed in by hand, for fills
you made in an outside paper account), watches it, closes it by rule, and
grades it. The grading is the point: the calibration table answers whether a
conviction of 80 actually wins more than a conviction of 50.

Fill model (documented so results are not flattering):
  entry = mid + 25% of the bid/ask spread
  exit  = mid - 25% of the bid/ask spread
You can override either with your own fill (entry_premium / exit_premium).

Automatic exits, checked while the market is open:
  stop           underlying trades through the stop level
  premium_stop   option has lost 50% of its entry price
  target         underlying reaches the target level
  time_exit      3 or fewer days to expiry (theta and pin risk)
  expired        past expiry, settled at intrinsic value

Needs DATABASE_URL (see app/db.py).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection, get_database_url
from app.routers import options as opt

router = APIRouter()

SLIPPAGE = 0.25
PREMIUM_STOP = 0.5
TIME_EXIT_DTE = 3


class OpenOptionTrade(BaseModel):
    ticker: str
    right: str  # call | put
    strike: float
    expiry: str  # YYYY-MM-DD
    contracts: int = 1
    conviction: float | None = None
    stop_underlying: float | None = None
    target_underlying: float | None = None
    factors: dict | None = None  # {"trend": 0.6, ...} snapshot at entry
    entry_premium: float | None = None  # your own fill, per share
    source: str = "model"  # model | manual
    notes: str | None = None


class CloseOptionTrade(BaseModel):
    exit_premium: float | None = None


# ---------------------------------------------------------------------------
# Pure logic (unit tested without a database)
# ---------------------------------------------------------------------------


def entry_fill(bid: float, ask: float) -> float:
    return round((bid + ask) / 2 + SLIPPAGE * (ask - bid), 2)


def exit_fill(bid: float, ask: float) -> float:
    return max(0.01, round((bid + ask) / 2 - SLIPPAGE * (ask - bid), 2))


def intrinsic(right: str, strike: float, spot: float) -> float:
    return max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)


def evaluate_exit(t: dict, spot: float, bid: float | None, ask: float | None, today: date, market_open: bool) -> dict | None:
    """Returns {"reason": ..., "exit_premium": ...} when the trade should close now, else None."""
    expiry = t["expiry"] if isinstance(t["expiry"], date) else date.fromisoformat(str(t["expiry"]))
    if expiry < today:
        return {"reason": "expired", "exit_premium": round(intrinsic(t["right"], t["strike"], spot), 2)}
    if not market_open:
        return None
    call = t["right"] == "call"
    stop, target = t.get("stop_underlying"), t.get("target_underlying")
    has_q = bid is not None and ask is not None and bid > 0 and ask > 0
    px = exit_fill(bid, ask) if has_q else None
    if stop is not None and ((call and spot <= stop) or (not call and spot >= stop)):
        return {"reason": "stop", "exit_premium": px}
    if has_q and px <= t["entry_premium"] * PREMIUM_STOP:
        return {"reason": "premium_stop", "exit_premium": px}
    if target is not None and ((call and spot >= target) or (not call and spot <= target)):
        return {"reason": "target", "exit_premium": px}
    if (expiry - today).days <= TIME_EXIT_DTE:
        return {"reason": "time_exit", "exit_premium": px}
    return None


def pnl(entry_premium: float, exit_premium: float, contracts: int) -> tuple[float, float]:
    return round((exit_premium - entry_premium) * 100 * contracts, 2), round((exit_premium / entry_premium - 1) * 100, 2)


def _summ(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "win_rate": None, "avg_pnl_pct": None, "total_pnl_usd": 0.0}
    wins = [r for r in rows if r["pnl_usd"] > 0]
    return {
        "n": len(rows), "win_rate": round(len(wins) / len(rows) * 100, 1),
        "avg_pnl_pct": round(sum(r["pnl_pct"] for r in rows) / len(rows), 1),
        "total_pnl_usd": round(sum(r["pnl_usd"] for r in rows), 2),
    }


def compute_stats(closed: list[dict]) -> dict:
    s = _summ(closed)
    if not closed:
        return {**s, "sample_note": "No closed trades yet", "by_direction": {}, "by_exit": {}, "by_source": {}, "calibration": [], "factors": {}, "equity": []}
    wins = [r for r in closed if r["pnl_usd"] > 0]
    losses = [r for r in closed if r["pnl_usd"] <= 0]
    gross_w = sum(r["pnl_usd"] for r in wins)
    gross_l = abs(sum(r["pnl_usd"] for r in losses))
    s.update({
        "avg_win_pct": round(sum(r["pnl_pct"] for r in wins) / len(wins), 1) if wins else None,
        "avg_loss_pct": round(sum(r["pnl_pct"] for r in losses) / len(losses), 1) if losses else None,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "expectancy_usd": round(sum(r["pnl_usd"] for r in closed) / len(closed), 2),
    })
    s["sample_note"] = ("Fewer than 30 closed trades: treat these numbers as anecdotes, not evidence"
                        if len(closed) < 30 else "Sample is large enough to start reading, not enough to trust blindly")

    def group(key):
        keys = sorted({str(key(r)) for r in closed})
        return {k: _summ([r for r in closed if str(key(r)) == k]) for k in keys}

    buckets = [("0-39", 0, 40), ("40-59", 40, 60), ("60-79", 60, 80), ("80-100", 80, 101)]
    calibration = []
    for label, lo, hi in buckets:
        rows = [r for r in closed if r.get("conviction") is not None and lo <= r["conviction"] < hi]
        calibration.append({"bucket": label, **_summ(rows)})

    factors: dict[str, dict] = {}
    names = sorted({k for r in closed for k in (r.get("factors") or {})})
    for name in names:
        aligned, against = [], []
        for r in closed:
            v = (r.get("factors") or {}).get(name)
            if v is None:
                continue
            sign = 1 if r["right"] == "call" else -1
            (aligned if v * sign >= 0.2 else against).append(r)
        factors[name] = {"aligned": _summ(aligned), "not_aligned": _summ(against)}

    ordered = sorted(closed, key=lambda r: r["exit_at"])
    cum, equity = 0.0, []
    for r in ordered:
        cum += r["pnl_usd"]
        equity.append({"t": r["exit_at"].isoformat() if hasattr(r["exit_at"], "isoformat") else str(r["exit_at"]), "pnl": round(cum, 2)})
    return {**s, "by_direction": group(lambda r: r["right"]), "by_exit": group(lambda r: r["exit_reason"] or "manual"),
            "by_source": group(lambda r: r["source"]), "calibration": calibration, "factors": factors, "equity": equity}


# ---------------------------------------------------------------------------
# Database glue
# ---------------------------------------------------------------------------


def _need_db():
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set. Options paper trading isn't configured")


def _close_row(cur, trade: dict, exit_premium: float, spot: float | None, reason: str):
    pnl_usd, pnl_pct = pnl(trade["entry_premium"], exit_premium, trade["contracts"])
    cur.execute(
        """UPDATE option_trades SET status='closed', exit_premium=%s, exit_underlying=%s, exit_at=%s,
           exit_reason=%s, pnl_usd=%s, pnl_pct=%s WHERE id=%s RETURNING *""",
        (exit_premium, spot, datetime.now(timezone.utc), reason, pnl_usd, pnl_pct, trade["id"]),
    )
    return cur.fetchone()


@router.post("/trades")
def open_trade(body: OpenOptionTrade):
    _need_db()
    right = body.right.lower()
    if right not in ("call", "put"):
        raise HTTPException(status_code=400, detail="right must be 'call' or 'put'")
    if body.contracts < 1 or body.contracts > 100:
        raise HTTPException(status_code=400, detail="contracts must be between 1 and 100")
    try:
        date.fromisoformat(body.expiry)
    except ValueError:
        raise HTTPException(status_code=400, detail="expiry must be YYYY-MM-DD")

    spot, _ = opt.spot_price(body.ticker)
    if body.entry_premium is not None:
        if body.entry_premium <= 0:
            raise HTTPException(status_code=400, detail="entry_premium must be positive")
        entry, source = round(body.entry_premium, 2), "manual" if body.source == "model" and body.notes else body.source
    else:
        if not opt.market_open_now():
            raise HTTPException(status_code=409, detail="Market is closed. Wait for the open, or record your own fill with entry_premium")
        q = opt.contract_quote(body.ticker, body.expiry, body.strike, right)
        if not (q["bid"] > 0 and q["ask"] > 0):
            raise HTTPException(status_code=409, detail="No two-sided quote on that contract right now. Try again or use your own fill")
        entry, source = entry_fill(q["bid"], q["ask"]), body.source

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO option_trades (ticker, "right", strike, expiry, contracts, entry_premium, entry_underlying,
               stop_underlying, target_underlying, conviction, factors, source, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) RETURNING *""",
            (body.ticker.upper(), right, body.strike, body.expiry, body.contracts, entry, spot,
             body.stop_underlying, body.target_underlying, body.conviction, json.dumps(body.factors or {}), source, body.notes),
        )
        return cur.fetchone()


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, body: CloseOptionTrade | None = None):
    _need_db()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM option_trades WHERE id=%s", (trade_id,))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail=f"No options trade {trade_id}")
        if t["status"] == "closed":
            raise HTTPException(status_code=400, detail="Trade is already closed")
        spot, _ = opt.spot_price(t["ticker"])
        if body and body.exit_premium is not None:
            px = round(body.exit_premium, 2)
        else:
            if date.fromisoformat(str(t["expiry"])) < datetime.now(opt.ET_TZ).date():
                px = round(intrinsic(t["right"], t["strike"], spot), 2)
            else:
                q = opt.contract_quote(t["ticker"], str(t["expiry"]), t["strike"], t["right"])
                if not (q["bid"] > 0 and q["ask"] > 0):
                    raise HTTPException(status_code=409, detail="No two-sided quote right now. Try later or give your own exit_premium")
                px = exit_fill(q["bid"], q["ask"])
        return _close_row(cur, t, px, spot, "manual")


@router.get("/trades")
def list_trades(status: str | None = None):
    _need_db()
    with get_connection() as conn, conn.cursor() as cur:
        if status in ("open", "closed"):
            cur.execute("SELECT * FROM option_trades WHERE status=%s ORDER BY entry_at DESC", (status,))
        else:
            cur.execute("SELECT * FROM option_trades ORDER BY entry_at DESC")
        rows = cur.fetchall()
    out = []
    spots: dict[str, float] = {}
    for r in rows:
        r = dict(r)
        if r["status"] == "open":
            try:
                if r["ticker"] not in spots:
                    spots[r["ticker"]] = opt.spot_price(r["ticker"], refresh=False)[0]
                q = opt.contract_quote(r["ticker"], str(r["expiry"]), r["strike"], r["right"], refresh=False)
                mark = (q["bid"] + q["ask"]) / 2 if q["bid"] > 0 and q["ask"] > 0 else q["last"]
                r["live"] = {"spot": spots[r["ticker"]], "mark": round(mark, 2), "as_of": q["fetched_at"],
                             "pnl_usd": round((mark - r["entry_premium"]) * 100 * r["contracts"], 2),
                             "pnl_pct": round((mark / r["entry_premium"] - 1) * 100, 1)}
            except Exception as e:
                r["live"] = {"error": str(e)[:120]}
        out.append(r)
    return {"trades": out}


@router.post("/trades/evaluate")
def evaluate_open_trades():
    """Apply the exit rules to every open trade. Safe to call repeatedly (the UI does, every few minutes)."""
    _need_db()
    is_open = opt.market_open_now()
    today = datetime.now(opt.ET_TZ).date()
    actions = []
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM option_trades WHERE status='open'")
        for t in cur.fetchall():
            try:
                spot, _ = opt.spot_price(t["ticker"])
                bid = ask = None
                if is_open and date.fromisoformat(str(t["expiry"])) >= today:
                    q = opt.contract_quote(t["ticker"], str(t["expiry"]), t["strike"], t["right"])
                    bid, ask = q["bid"], q["ask"]
                verdict = evaluate_exit(t, spot, bid, ask, today, is_open)
                if verdict and verdict["exit_premium"] is not None:
                    row = _close_row(cur, t, verdict["exit_premium"], spot, verdict["reason"])
                    actions.append({"id": t["id"], "ticker": t["ticker"], "reason": verdict["reason"], "pnl_usd": row["pnl_usd"], "pnl_pct": row["pnl_pct"]})
            except Exception as e:
                actions.append({"id": t["id"], "ticker": t["ticker"], "error": str(e)[:160]})
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "market_open": is_open, "actions": actions}


@router.get("/stats")
def get_stats():
    _need_db()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM option_trades WHERE status='closed'")
        closed = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(entry_premium*100*contracts),0) AS at_risk FROM option_trades WHERE status='open'")
        o = cur.fetchone()
    return {**compute_stats(closed), "open_positions": o["n"], "open_premium_at_risk_usd": round(o["at_risk"], 2)}
