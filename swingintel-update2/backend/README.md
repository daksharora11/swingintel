# SwingIntel backend

FastAPI backend for the three frontend modules. Each router maps 1:1 to a tab:

| Endpoint | Frontend tab | Data source | Needs a key? |
|---|---|---|---|
| `GET /api/prices/{ticker}` | Live & Prediction | Yahoo Finance via `yfinance` | No |
| `GET /api/prices/{ticker}/history` | Live & Prediction (sparkline) | Yahoo Finance via `yfinance` | No |
| `GET /api/markets` | Prediction Markets | Polymarket Gamma API | No |
| `GET /api/markets/arbitrage` | Prediction Markets (arb panel) | Polymarket Gamma API | No |
| `GET /api/news/{ticker}` | Sentiment | NewsAPI.org | Yes — `NEWSAPI_KEY` |

## Setup

```bash
pip install -r requirements.txt
export NEWSAPI_KEY=your_key_here   # free tier at newsapi.org
uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000/docs` for interactive API docs.

## What's been verified vs. not

This was built and route-tested (compilation, route registration, error
handling) inside a sandboxed environment whose network is restricted to
package registries — it can't reach Yahoo Finance, Polymarket, or NewsAPI.
So:

- **Verified**: the app boots, every route registers correctly, and each
  endpoint fails *cleanly* (proper status code + message) rather than
  crashing when the underlying request can't go through.
- **Not verified from here**: the actual response shapes coming back from
  Yahoo Finance, Polymarket's Gamma API, and NewsAPI. These are built
  against each service's documented format, but external APIs drift —
  run this somewhere with normal internet access and check the first
  real response against what each router expects before trusting it.
  The likeliest thing to need a small fix is `markets.py`'s assumption
  that `outcomePrices` is always a 2-element `[yes, no]` array — Polymarket
  has non-binary markets too, and this skips anything that doesn't match.

## Known simplifications, on purpose

- **News sentiment** uses the same keyword-lexicon scorer as the frontend
  mock, not a real NLP model. It's there so the pipeline is complete
  end-to-end. Swap `score_headline()` in `news.py` for a real model
  (`ProsusAI/finbert` on Hugging Face is a common starting point) —
  every caller only depends on `{score, magnitude}`, so nothing else changes.
- **Nested-market pairs** for the arbitrage check (e.g. "cuts rates" vs.
  "cuts by 50bps+") aren't discoverable from Polymarket's API — there's no
  field that encodes "this market is a subset of that one." `NESTED_PAIRS`
  in `markets.py` is where you'd curate that by hand for the specific
  markets you're tracking.
- **Live prices** here means polling `yfinance`, not a streaming feed —
  fine for a personal swing-trading tool checking every few seconds, not
  fine for anything latency-sensitive. Polygon.io or Finnhub websockets
  are the upgrade path if that ever matters.
