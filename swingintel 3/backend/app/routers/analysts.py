"""
Analyst ratings — Finnhub's recommendation-trends endpoint (free tier).
Returns the most recent period's counts of strongBuy/buy/hold/sell/
strongSell across covering analysts.

Scoring: a weighted average where strongBuy/strongSell count double a
plain buy/sell — a standard, simple way to turn a 5-bucket distribution
into a single -1..1 number without hiding the underlying counts (they're
returned alongside the score).
"""

import os
from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

RECOMMENDATION_URL = "https://finnhub.io/api/v1/stock/recommendation"


@router.get("/{ticker}")
def get_analyst_recommendations(ticker: str):
    api_key = os.environ.get("FINNHUB_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="FINNHUB_KEY not set in environment")

    try:
        resp = requests.get(
            RECOMMENDATION_URL,
            params={"symbol": ticker.upper(), "token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Finnhub request failed: {e}")

    periods = resp.json()
    if not periods:
        raise HTTPException(status_code=404, detail=f"No analyst data for '{ticker}'")

    latest = periods[0]  # Finnhub returns most recent period first
    strong_buy = latest.get("strongBuy", 0)
    buy = latest.get("buy", 0)
    hold = latest.get("hold", 0)
    sell = latest.get("sell", 0)
    strong_sell = latest.get("strongSell", 0)

    weighted_sum = (strong_buy * 2) + buy - sell - (strong_sell * 2)
    total = strong_buy + buy + hold + sell + strong_sell
    score = weighted_sum / (total * 2) if total else 0.0

    return {
        "ticker": ticker.upper(),
        "period": latest.get("period"),
        "score": round(score, 2),
        "strongBuy": strong_buy,
        "buy": buy,
        "hold": hold,
        "sell": sell,
        "strongSell": strong_sell,
        "total_analysts": total,
    }
