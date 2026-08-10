"""
Insider trading — Finnhub's insider-transactions endpoint, sourced from
real SEC Form 3/4/5 filings. Free tier covers this (60 calls/min).

Scoring approach: rather than summing raw share counts (which scales
wildly differently for a small-cap vs. a mega-cap), this counts
transactions as buys or sells and scores by the balance between them.
That's a deliberate simplification — a single large sale by one insider
counts the same as a single small purchase by another — but it avoids
the worse failure mode of one huge routine sale (e.g. a scheduled 10b5-1
plan) dominating the score just because of its size.

Needs FINNHUB_KEY in the environment.
"""

import os
from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

FINNHUB_URL = "https://finnhub.io/api/v1/stock/insider-transactions"


@router.get("/{ticker}")
def get_insider_transactions(ticker: str, limit: int = 50):
    api_key = os.environ.get("FINNHUB_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="FINNHUB_KEY not set in environment")

    try:
        resp = requests.get(
            FINNHUB_URL,
            params={"symbol": ticker.upper(), "token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Finnhub request failed: {e}")

    data = resp.json().get("data", [])[:limit]

    buys = [t for t in data if (t.get("change") or 0) > 0]
    sells = [t for t in data if (t.get("change") or 0) < 0]
    total = len(buys) + len(sells)

    # Balance of buy vs. sell transactions, not raw share volume — see
    # module docstring for why.
    score = (len(buys) - len(sells)) / total if total else 0.0
    net_shares = sum(t.get("change") or 0 for t in data)

    return {
        "ticker": ticker.upper(),
        "score": round(score, 2),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "net_shares": net_shares,
        "transactions": [
            {
                "name": t.get("name"),
                "change": t.get("change"),
                "transactionDate": t.get("transactionDate"),
                "transactionCode": t.get("transactionCode"),
                "transactionPrice": t.get("transactionPrice"),
            }
            for t in data[:10]
        ],
    }
