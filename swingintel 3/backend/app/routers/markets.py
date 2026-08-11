"""
Prediction markets — Polymarket's public Gamma API (no key required for
read access).

IMPORTANT PARSING QUIRK: Gamma ships `outcomePrices`, `outcomes`, and
`clobTokenIds` as JSON-encoded STRINGS, not native arrays — e.g. the
field's raw value is the literal text '["0.62","0.38"]', not a real
array. Every field access below goes through json.loads() first. Code
that indexes into these fields without parsing will silently misbehave
(iterating over characters of a string, not array elements) — this is a
common, easy-to-miss bug against this API.

No fabricated fields: this router only ever returns numbers that came
directly from Polymarket. Notably, Gamma's basic /markets endpoint does
NOT expose a 24-hour price-change figure — only current price and
volume — so this deliberately does not invent one. `volume_24hr` (real
trading volume, not price movement) is exposed instead.
"""

import json
from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

GAMMA_API = "https://gamma-api.polymarket.com/markets"

# Map your own tracked nested-market relationships here, e.g.
# {"child-market-id": "parent-market-id"}. Polymarket has no built-in
# concept of nesting, so this has to be curated by hand per market pair
# you care about, using real IDs found via the search endpoint below.
NESTED_PAIRS: dict[str, str] = {}


def _parse_market(m: dict) -> dict | None:
    """Turns a raw Gamma market object into clean, typed fields. Returns
    None (rather than a guessed/partial value) if the market doesn't have
    the binary YES/NO shape this app is built around."""
    try:
        outcomes = json.loads(m.get("outcomes", "[]"))
        prices = json.loads(m.get("outcomePrices", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None

    if len(outcomes) != 2 or len(prices) != 2:
        return None  # multi-outcome market — not handled here, not faked either

    try:
        price_map = {outcomes[i].lower(): float(prices[i]) for i in range(2)}
        yes_price = price_map.get("yes")
        no_price = price_map.get("no")
    except (ValueError, TypeError, AttributeError):
        return None

    if yes_price is None or no_price is None:
        return None

    def _num(key):
        val = m.get(key)
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    return {
        "id": m.get("id"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "yes": yes_price,
        "no": no_price,
        "volume": _num("volume"),
        "volume_24hr": _num("volume24hr"),
        "liquidity": _num("liquidity"),
        "end_date": m.get("endDate"),
        "active": m.get("active"),
    }


@router.get("")
def list_markets(search: str = "", limit: int = 20):
    """
    Live markets from Polymarket, optionally filtered by a search term
    matched against the question text. Every field in the response comes
    straight from Polymarket — markets that don't parse cleanly into the
    binary yes/no shape are dropped, not guessed at.
    """
    try:
        resp = requests.get(GAMMA_API, params={"limit": max(limit, 100), "active": "true", "closed": "false"}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Polymarket request failed: {e}")

    raw_markets = resp.json()
    if search:
        raw_markets = [m for m in raw_markets if search.lower() in (m.get("question") or "").lower()]

    parsed = [p for p in (_parse_market(m) for m in raw_markets) if p is not None]
    return parsed[:limit]


@router.get("/arbitrage")
def detect_arbitrage(limit: int = 200):
    """
    Runs two arbitrage checks against live, correctly-parsed Polymarket
    data:
      1. Same-market mispricing: YES + NO should sum to ~$1.00.
      2. Nested-event mispricing: a specific outcome can't be priced above
         the broader outcome it sits inside (needs NESTED_PAIRS filled in
         with real market IDs — empty by default, since fabricating a
         relationship between markets we haven't verified would be
         exactly the kind of made-up data this endpoint exists to avoid).
    """
    try:
        resp = requests.get(GAMMA_API, params={"limit": limit, "active": "true", "closed": "false"}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Polymarket request failed: {e}")

    raw_markets = resp.json()
    markets = {}
    for m in raw_markets:
        parsed = _parse_market(m)
        if parsed:
            markets[parsed["id"]] = parsed

    flags = []
    for m in markets.values():
        total = m["yes"] + m["no"]
        if abs(total - 1) > 0.03:
            flags.append({
                "type": "risk_free" if total < 1 else "overpriced",
                "market_id": m["id"],
                "question": m["question"],
                "yes": m["yes"],
                "no": m["no"],
                "detail": f"YES + NO = {total:.2f}",
            })

    for child_id, parent_id in NESTED_PAIRS.items():
        child, parent = markets.get(child_id), markets.get(parent_id)
        if not child or not parent:
            continue
        if child["yes"] > parent["yes"]:
            flags.append({
                "type": "nested_mispricing",
                "child_question": child["question"],
                "parent_question": parent["question"],
                "child_yes": child["yes"],
                "parent_yes": parent["yes"],
            })

    return {"flags": flags, "markets_checked": len(markets)}
