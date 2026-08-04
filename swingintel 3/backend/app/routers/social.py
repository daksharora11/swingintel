"""
Social buzz — ApeWisdom's public API (apewisdom.io), no key required.
Tracks Reddit mention volume across r/wallstreetbets, r/stocks, and
similar communities, refreshed roughly every 30 minutes on their end.

IMPORTANT: this is mention volume and rank, NOT sentiment polarity.
Real Reddit sentiment scoring (bullish/bearish, not just "how much are
people talking about this") is a paid feature everywhere it's offered.
Don't relabel this as "bullish/bearish" upstream — it measures attention,
which can precede a rally or a crash equally.

ApeWisdom's endpoint is a ranked, paginated list (not a per-ticker
lookup), so finding one ticker means scanning pages until it turns up.
Most large-caps that get discussed at all show up in the first page or
two; if a ticker isn't found within max_pages, that itself is
informative — it's not currently part of the Reddit conversation.
"""

from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/{filter}/page/{page}"


@router.get("/{ticker}")
def get_social_buzz(ticker: str, filter: str = "all-stocks", max_pages: int = 3):
    ticker_upper = ticker.upper()

    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(
                APEWISDOM_URL.format(filter=filter, page=page),
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"ApeWisdom request failed: {e}")

        body = resp.json()
        for entry in body.get("results", []):
            if entry.get("ticker", "").upper() == ticker_upper:
                mentions = int(entry.get("mentions") or 0)
                mentions_24h_ago = int(entry.get("mentions_24h_ago") or 0)
                return {
                    "ticker": ticker_upper,
                    "found": True,
                    "rank": entry.get("rank"),
                    "rank_24h_ago": entry.get("rank_24h_ago"),
                    "mentions": mentions,
                    "mentions_24h_ago": mentions_24h_ago,
                    "mentions_change_pct": (
                        round((mentions - mentions_24h_ago) / mentions_24h_ago * 100, 1)
                        if mentions_24h_ago else None
                    ),
                    "upvotes": entry.get("upvotes"),
                }

    return {
        "ticker": ticker_upper,
        "found": False,
        "note": f"Not in the top mentioned tickers across {max_pages} pages — low or no current Reddit chatter.",
    }
