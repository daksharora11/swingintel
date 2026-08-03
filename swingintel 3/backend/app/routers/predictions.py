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
