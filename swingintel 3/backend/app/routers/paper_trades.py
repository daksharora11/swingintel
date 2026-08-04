"""
Paper trading — simulated positions with no real money, opened either
manually or directly from a fired signal, closed manually against a real
live price. This is the accuracy-measurement loop: open a trade at the
composite score's suggested direction, close it later, and the stats
endpoint tells you how often Call vs. Put actually worked out.

Deliberately manual open/close rather than auto-closing after a fixed
horizon — a real trader decides when to exit, and forcing an arbitrary
"always close after 1 day" rule would measure that rule more than the
signal itself. Entry and exit prices are always pulled fresh from
yfinance at the moment of the action — never estimated, never carried
over from a stale cache — so the P&L reflects what a real fill would
have looked like at that moment, not a fabricated number.

Needs DATABASE_URL in the environment (see app/db.py).
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import yfinance as yf

from app.db import get_connection, get_database_url

router = APIRouter()


class OpenTrade(BaseModel):
    ticker: str
    direction: str  # "call" or "put"
    signal_composite_score: float | None = None


def _live_price(ticker: str) -> float:
    """Always fetches a fresh quote — never a cached or estimated value.
    Raises if the real price can't be fetched, rather than silently
    substituting anything in its place."""
    try:
        t = yf.Ticker(ticker.upper())
        price = t.fast_info["last_price"]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch live price for '{ticker}': {e}")
    if price is None:
        raise HTTPException(status_code=502, detail=f"No live price available for '{ticker}'")
    return float(price)


@router.post("")
def open_trade(entry: OpenTrade):
    if entry.direction not in ("call", "put"):
        raise HTTPException(status_code=400, detail="direction must be 'call' or 'put'")
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set — paper trading isn't configured")

    entry_price = _live_price(entry.ticker)  # real price at the moment of opening, always

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO paper_trades (ticker, direction, entry_price, signal_composite_score)
                VALUES (%s, %s, %s, %s)
                RETURNING id, entry_at
                """,
                (entry.ticker.upper(), entry.direction, entry_price, entry.signal_composite_score),
            )
            row = cur.fetchone()

    return {"id": row["id"], "ticker": entry.ticker.upper(), "direction": entry.direction,
            "entry_price": entry_price, "entry_at": row["entry_at"]}


@router.post("/{trade_id}/close")
def close_trade(trade_id: int):
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set — paper trading isn't configured")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM paper_trades WHERE id = %s", (trade_id,))
            trade = cur.fetchone()
            if not trade:
                raise HTTPException(status_code=404, detail=f"No paper trade with id {trade_id}")
            if trade["status"] == "closed":
                raise HTTPException(status_code=400, detail="Trade is already closed")

            exit_price = _live_price(trade["ticker"])  # real price at the moment of closing, always
            raw_move = (exit_price - trade["entry_price"]) / trade["entry_price"]
            pnl_pct = raw_move * 100 if trade["direction"] == "call" else -raw_move * 100

            cur.execute(
                """
                UPDATE paper_trades
                SET status = 'closed', exit_price = %s, exit_at = %s, pnl_pct = %s
                WHERE id = %s
                RETURNING id, ticker, direction, entry_price, entry_at, exit_price, exit_at, pnl_pct
                """,
                (exit_price, datetime.now(timezone.utc), pnl_pct, trade_id),
            )
            updated = cur.fetchone()

    return dict(updated)


@router.get("")
def list_trades(status: str | None = None):
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set — paper trading isn't configured")

    with get_connection() as conn:
        with conn.cursor() as cur:
            if status in ("open", "closed"):
                cur.execute("SELECT * FROM paper_trades WHERE status = %s ORDER BY entry_at DESC", (status,))
            else:
                cur.execute("SELECT * FROM paper_trades ORDER BY entry_at DESC")
            rows = cur.fetchall()

    return {"trades": [dict(r) for r in rows]}


@router.get("/stats")
def get_stats():
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set — paper trading isn't configured")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT direction, pnl_pct FROM paper_trades WHERE status = 'closed'")
            closed = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM paper_trades WHERE status = 'open'")
            open_count = cur.fetchone()["n"]

    def win_rate(rows):
        if not rows:
            return None
        wins = sum(1 for r in rows if r["pnl_pct"] > 0)
        return round(wins / len(rows) * 100, 1)

    calls = [r for r in closed if r["direction"] == "call"]
    puts = [r for r in closed if r["direction"] == "put"]

    return {
        "total_closed": len(closed),
        "open_positions": open_count,
        "overall_win_rate": win_rate(closed),
        "call_win_rate": win_rate(calls),
        "call_count": len(calls),
        "put_win_rate": win_rate(puts),
        "put_count": len(puts),
        "avg_pnl_pct": round(sum(r["pnl_pct"] for r in closed) / len(closed), 2) if closed else None,
    }
