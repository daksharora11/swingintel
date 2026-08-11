"""
Prediction history — logs each composite score (and its factor breakdown)
alongside the price at that moment, then lets you compare it against what
actually happened.

Logging happens per-ticker whenever the frontend refreshes that ticker's
factors (see the frontend's refreshFactors) — not on a fixed schedule.
That means history builds up from actual usage, not a guaranteed daily
cadence. For unattended daily snapshots regardless of whether anyone has
the app open, the real next step is a scheduled job (e.g. a Render Cron
Job hitting a /log endpoint once a day) — not built here, since that's a
separate piece of infrastructure from the API itself.

Needs DATABASE_URL in the environment (see app/db.py for why Neon, not
Render's free Postgres).
"""

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import yfinance as yf

from app.db import get_connection, get_database_url

router = APIRouter()


class PredictionLog(BaseModel):
    ticker: str
    composite_score: float
    price: float
    news_score: float | None = None
    event_score: float | None = None
    momentum_score: float | None = None
    insider_score: float | None = None
    analyst_score: float | None = None
    fundamentals_score: float | None = None


@router.post("/log")
def log_prediction(entry: PredictionLog):
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set — prediction history isn't configured")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO predictions
                    (ticker, composite_score, news_score, event_score, momentum_score,
                     insider_score, analyst_score, fundamentals_score, price_at_prediction)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, predicted_at
                """,
                (
                    entry.ticker.upper(), entry.composite_score, entry.news_score, entry.event_score,
                    entry.momentum_score, entry.insider_score, entry.analyst_score,
                    entry.fundamentals_score, entry.price,
                ),
            )
            row = cur.fetchone()

    return {"id": row["id"], "predicted_at": row["predicted_at"]}


@router.get("/history/{ticker}")
def get_prediction_history(ticker: str, days: int = 30):
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set — prediction history isn't configured")

    since = datetime.now(timezone.utc) - timedelta(days=days)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, predicted_at, composite_score, price_at_prediction
                FROM predictions
                WHERE ticker = %s AND predicted_at >= %s
                ORDER BY predicted_at ASC
                """,
                (ticker.upper(), since),
            )
            rows = cur.fetchall()

    if not rows:
        return {"ticker": ticker.upper(), "days": days, "points": []}

    # Compare each logged prediction's price against the current price to
    # get the actual move since that prediction was made.
    try:
        t = yf.Ticker(ticker.upper())
        current_price = t.fast_info["last_price"]
    except Exception:
        current_price = None

    points = []
    for r in rows:
        actual_change_pct = None
        if current_price is not None and r["price_at_prediction"]:
            actual_change_pct = round(
                (current_price - r["price_at_prediction"]) / r["price_at_prediction"] * 100, 2
            )
        points.append({
            "id": r["id"],
            "predicted_at": r["predicted_at"],
            "composite_score": r["composite_score"],
            "price_at_prediction": r["price_at_prediction"],
            "current_price": current_price,
            "actual_change_pct": actual_change_pct,
            # Did the prediction's direction match what actually happened?
            # Only meaningful once there's been some real price movement.
            "direction_correct": (
                (r["composite_score"] > 0.1 and actual_change_pct > 0)
                or (r["composite_score"] < -0.1 and actual_change_pct < 0)
                if actual_change_pct is not None else None
            ),
        })

    return {"ticker": ticker.upper(), "days": days, "current_price": current_price, "points": points}


@router.get("/backtest")
def backtest(days: int = 30, min_abs_score: float = 0.0):
    """Aggregate backtest across every ticker's logged predictions.

    This deliberately reuses the same predictions table the live
    prediction-history chart reads from — it's not a separate synthetic
    backtest, so it only ever reflects real logged signals from actual
    Refresh clicks, and will be sparse until there's enough usage history.
    `min_abs_score` lets you see how accuracy/return would look if you'd
    only acted on signals at or above a given composite-score strength —
    the same kind of threshold the trade-signal panel uses.
    """
    if not get_database_url():
        raise HTTPException(status_code=503, detail="DATABASE_URL not set — backtesting isn't configured")

    since = datetime.now(timezone.utc) - timedelta(days=days)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ticker, predicted_at, composite_score, price_at_prediction
                FROM predictions
                WHERE predicted_at >= %s AND ABS(composite_score) >= %s
                ORDER BY predicted_at ASC
                """,
                (since, min_abs_score),
            )
            rows = cur.fetchall()

    if not rows:
        return {
            "days": days, "min_abs_score": min_abs_score, "total_signals": 0,
            "decided_signals": 0, "direction_accuracy_pct": None,
            "avg_simulated_return_pct": None, "by_ticker": {}, "points": [],
        }

    # One live price fetch per unique ticker, not per row — a ticker with
    # 20 logged predictions still only costs one yfinance call.
    tickers = sorted({r["ticker"] for r in rows})
    current_prices = {}
    for t in tickers:
        try:
            current_prices[t] = yf.Ticker(t).fast_info["last_price"]
        except Exception:
            current_prices[t] = None

    points = []
    cumulative = 0.0
    for r in rows:
        cur_price = current_prices.get(r["ticker"])
        actual_change_pct = None
        if cur_price is not None and r["price_at_prediction"]:
            actual_change_pct = round(
                (cur_price - r["price_at_prediction"]) / r["price_at_prediction"] * 100, 2
            )
        direction = "call" if r["composite_score"] > 0 else ("put" if r["composite_score"] < 0 else None)
        # Simulated return of an equal-weighted directional bet, sized 1
        # unit per signal — this is the accuracy/return backbone; actual
        # dollar P&L with real position sizes lives in /api/paper-trades.
        sim_return_pct = None
        direction_correct = None
        if actual_change_pct is not None and direction:
            sim_return_pct = actual_change_pct if direction == "call" else -actual_change_pct
            direction_correct = sim_return_pct > 0
            cumulative += sim_return_pct

        points.append({
            "id": r["id"],
            "ticker": r["ticker"],
            "predicted_at": r["predicted_at"],
            "composite_score": r["composite_score"],
            "direction": direction,
            "price_at_prediction": r["price_at_prediction"],
            "current_price": cur_price,
            "actual_change_pct": actual_change_pct,
            "sim_return_pct": sim_return_pct,
            "direction_correct": direction_correct,
            "cumulative_sim_return_pct": round(cumulative, 2) if sim_return_pct is not None else None,
        })

    decided = [p for p in points if p["direction_correct"] is not None]
    correct = [p for p in decided if p["direction_correct"]]
    accuracy = round(len(correct) / len(decided) * 100, 1) if decided else None
    avg_return = round(sum(p["sim_return_pct"] for p in decided) / len(decided), 2) if decided else None

    by_ticker = {}
    for t in tickers:
        tp = [p for p in decided if p["ticker"] == t]
        if tp:
            by_ticker[t] = {
                "count": len(tp),
                "accuracy_pct": round(sum(1 for p in tp if p["direction_correct"]) / len(tp) * 100, 1),
                "avg_return_pct": round(sum(p["sim_return_pct"] for p in tp) / len(tp), 2),
            }

    return {
        "days": days,
        "min_abs_score": min_abs_score,
        "total_signals": len(rows),
        "decided_signals": len(decided),
        "direction_accuracy_pct": accuracy,
        "avg_simulated_return_pct": avg_return,
        "by_ticker": by_ticker,
        "points": points,
    }
