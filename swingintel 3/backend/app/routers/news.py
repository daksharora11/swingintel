"""
News + sentiment — pulls headlines from NewsAPI.org and scores them with a
small keyword lexicon (same approach as the frontend mock, so behavior is
consistent while you're wiring things up).

This lexicon scorer is a deliberately low bar — it's here so the pipeline
is complete end-to-end. For anything you'd actually trade on, replace
`score_headline` with a real model call, e.g. a finance-tuned sentiment
model from Hugging Face (ProsusAI/finbert is a common choice) run locally,
or a hosted NLP sentiment API. Every caller here only depends on
{"score": float in [-1,1], "magnitude": float in [0,1]}, so swapping the
implementation doesn't touch anything else.

Needs an environment variable NEWSAPI_KEY (free tier at newsapi.org).
"""

import os
from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

NEWSAPI_URL = "https://newsapi.org/v2/everything"

POS_WORDS = ["beats", "surges", "raises", "approval", "expands", "record", "strong", "upgrade", "wins", "growth", "rally", "boosts"]
NEG_WORDS = ["misses", "cuts", "probe", "tariff", "delays", "recall", "downgrade", "lawsuit", "slump", "warns", "halts", "shortfall"]


def score_headline(text: str) -> dict:
    lower = text.lower()
    score = sum(1 for w in POS_WORDS if w in lower) - sum(1 for w in NEG_WORDS if w in lower)
    magnitude = min(1.0, abs(score) / 2.5 + 0.15)
    return {"score": max(-1.0, min(1.0, score / 2)), "magnitude": round(magnitude, 2)}


@router.get("/{ticker}")
def get_news(ticker: str, page_size: int = 15):
    api_key = os.environ.get("NEWSAPI_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="NEWSAPI_KEY not set in environment")

    try:
        resp = requests.get(
            NEWSAPI_URL,
            params={
                "q": ticker,
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": page_size,
                "apiKey": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"NewsAPI request failed: {e}")

    articles = resp.json().get("articles", [])
    scored = []
    for a in articles:
        headline = a.get("title") or ""
        scored.append({
            "ticker": ticker.upper(),
            "source": (a.get("source") or {}).get("name"),
            "time": a.get("publishedAt"),
            "headline": headline,
            "url": a.get("url"),
            **score_headline(headline),
        })

    return {"ticker": ticker.upper(), "articles": scored}
