"""
Prediction markets — pulls from Polymarket's public Gamma API (no key
required for read access) and runs the same two arbitrage checks used in
the frontend's Prediction Markets tab:

  1. Same-market mispricing: YES + NO should sum to ~$1.00.
  2. Nested-event mispricing: a specific outcome can't be priced above the
     broader outcome it sits inside — requires you to define which markets
     are nested under which (Polymarket doesn't encode that relationship
     itself, so `NESTED_PAIRS` below is where you'd maintain it).
"""

from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

GAMMA_API = "https://gamma-api.polymarket.com/markets"

# Map your own tracked nested-market relationships here, e.g.
# {"child-market-slug": "parent-market-slug"}. Polymarket has no built-in
# concept of nesting, so this has to be curated by hand per market pair
# you care about.
NESTED_PAIRS: dict[str, str] = {}


@router.get("")
def list_markets(search: str = "", limit: int = 20):
    """
    Raw market list from Polymarket, optionally filtered by a search term
    matched against the question text (done client-side here since Gamma's
    query params vary by deployment — check their docs for server-side
    filters before scaling this up).
    """
    try:
        resp = requests.get(GAMMA_API, params={"limit": limit, "active": "true"}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Polymarket request failed: {e}")

    markets = resp.json()
    if search:
        markets = [m for m in markets if search.lower() in m.get("question", "").lower()]
    return markets


@router.get("/arbitrage")
def detect_arbitrage(limit: int = 100):
    """
    Runs the two arbitrage checks against live Polymarket data.
    NOTE: Gamma's response shape varies by market type (binary vs
    multi-outcome) — this assumes binary YES/NO markets with an
    `outcomePrices` field, which covers most political/macro markets but
    not every listing. Validate the shape against a live response before
    relying on this for anything real.
    """
    try:
        resp = requests.get(GAMMA_API, params={"limit": limit, "active": "true"}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Polymarket request failed: {e}")

    markets = {m["id"]: m for m in resp.json() if "outcomePrices" in m}
    flags = []

    for m in markets.values():
        try:
            prices = [float(p) for p in m["outcomePrices"]]
        except (KeyError, ValueError, TypeError):
            continue
        if len(prices) != 2:
            continue
        yes, no = prices
        total = yes + no
        if abs(total - 1) > 0.03:
            flags.append({
                "type": "risk_free" if total < 1 else "overpriced",
                "market_id": m["id"],
                "question": m.get("question"),
                "yes": yes,
                "no": no,
                "detail": f"YES + NO = {total:.2f}",
            })

    for child_id, parent_id in NESTED_PAIRS.items():
        child, parent = markets.get(child_id), markets.get(parent_id)
        if not child or not parent:
            continue
        try:
            child_yes = float(child["outcomePrices"][0])
            parent_yes = float(parent["outcomePrices"][0])
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if child_yes > parent_yes:
            flags.append({
                "type": "nested_mispricing",
                "child_question": child.get("question"),
                "parent_question": parent.get("question"),
                "child_yes": child_yes,
                "parent_yes": parent_yes,
            })

    return {"flags": flags, "markets_checked": len(markets)}
