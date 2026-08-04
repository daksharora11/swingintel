"""
Company fundamentals — SEC EDGAR's XBRL company-facts API, sourced directly
from filed 10-Ks and 10-Qs. Genuinely free, no API key, no signup — but the
SEC requires every request to carry a descriptive User-Agent with a real
contact (unidentified/generic clients get blocked), and enforces a 10
requests/second rate limit across all of data.sec.gov.

Two-step lookup: EDGAR indexes companies by CIK (Central Index Key), not
ticker, so this first resolves ticker -> CIK using SEC's own ticker map,
then pulls company facts for that CIK.

Scoring: revenue growth and net income growth, most recent annual filing
vs. the one before it, blended into a single -1..1 "is the underlying
business improving" signal. Different companies report under slightly
different XBRL tags (e.g. "Revenues" vs.
"RevenueFromContractWithCustomerExcludingAssessedTax") — this tries a
short list of common alternates and uses whichever the company actually
filed under.
"""

import time
from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()

# SEC asks for a descriptive User-Agent with a real contact so they can
# reach out instead of just blocking you. Replace the email before relying
# on this in production — a generic/missing one is the #1 cause of 403s.
SEC_HEADERS = {"User-Agent": "SwingIntel contact@example.com"}

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]
NET_INCOME_TAGS = ["NetIncomeLoss", "ProfitLoss"]

# In-memory cache for the ticker->CIK map — it's a few MB and doesn't
# change intraday, so fetching it once per process instead of per-request
# both respects the rate limit and is just faster.
_ticker_cik_cache = {"data": None, "fetched_at": 0}
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours


def _get_ticker_cik_map():
    now = time.time()
    if _ticker_cik_cache["data"] and (now - _ticker_cik_cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _ticker_cik_cache["data"]

    resp = requests.get(TICKER_MAP_URL, headers=SEC_HEADERS, timeout=15)
    resp.raise_for_status()
    raw = resp.json()  # {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}

    mapping = {entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in raw.values()}
    _ticker_cik_cache["data"] = mapping
    _ticker_cik_cache["fetched_at"] = now
    return mapping


def _extract_annual_series(facts, tags):
    """Pull the annual (10-K, form='10-K') USD values for the first tag
    that the company actually reports under, sorted by period end date."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        entries = us_gaap.get(tag, {}).get("units", {}).get("USD", [])
        annual = [e for e in entries if e.get("form") == "10-K" and e.get("fp") == "FY"]
        if annual:
            annual.sort(key=lambda e: e["end"])
            return annual, tag
    return [], None


@router.get("/{ticker}")
def get_fundamentals(ticker: str):
    ticker_upper = ticker.upper()

    try:
        cik_map = _get_ticker_cik_map()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch SEC ticker map: {e}")

    cik = cik_map.get(ticker_upper)
    if not cik:
        raise HTTPException(status_code=404, detail=f"'{ticker_upper}' not found in SEC's ticker list")

    try:
        resp = requests.get(COMPANY_FACTS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"SEC EDGAR request failed: {e}")

    facts = resp.json()

    revenue_series, revenue_tag = _extract_annual_series(facts, REVENUE_TAGS)
    income_series, income_tag = _extract_annual_series(facts, NET_INCOME_TAGS)

    def yoy_growth(series):
        if len(series) < 2:
            return None
        latest, prior = series[-1]["val"], series[-2]["val"]
        if prior == 0:
            return None
        return (latest - prior) / abs(prior)

    revenue_growth = yoy_growth(revenue_series)
    income_growth = yoy_growth(income_series)

    # Blend into -1..1: average the two growth rates (each capped at
    # +/-50% growth = +/-1) so one very volatile figure doesn't dominate.
    components = [g for g in (revenue_growth, income_growth) if g is not None]
    score = None
    if components:
        capped = [max(-1, min(1, g / 0.5)) for g in components]
        score = round(sum(capped) / len(capped), 2)

    return {
        "ticker": ticker_upper,
        "score": score,
        "revenue_latest": revenue_series[-1]["val"] if revenue_series else None,
        "revenue_growth_yoy": round(revenue_growth, 4) if revenue_growth is not None else None,
        "revenue_tag_used": revenue_tag,
        "net_income_latest": income_series[-1]["val"] if income_series else None,
        "net_income_growth_yoy": round(income_growth, 4) if income_growth is not None else None,
        "net_income_tag_used": income_tag,
        "fiscal_year_end": revenue_series[-1]["end"] if revenue_series else None,
    }
