"""
SwingIntel backend — FastAPI entrypoint.

Eight routers:
  /api/prices       -> live/quote data + short interest (yfinance, no key)
  /api/markets      -> Polymarket odds + arbitrage detection (public API, no key)
  /api/news         -> headlines + sentiment scoring (needs NEWSAPI_KEY)
  /api/insider      -> insider buy/sell transactions (needs FINNHUB_KEY)
  /api/analysts     -> analyst recommendation trends (needs FINNHUB_KEY)
  /api/social       -> Reddit mention volume/rank via ApeWisdom (no key)
  /api/fundamentals -> real 10-K/10-Q financials via SEC EDGAR (no key)
  /api/predictions  -> log + compare predictions over time (needs DATABASE_URL)

Run locally:
    uvicorn app.main:app --reload --port 8000

Note: this environment's network is sandboxed to package registries, so the
outbound calls in these routers were NOT tested live here — the code is
correct against each service's documented API shape, but run it in an
environment with normal internet access to actually pull real data.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()  # reads .env — put NEWSAPI_KEY / FINNHUB_KEY / DATABASE_URL here instead of exporting manually

from app.routers import prices, markets, news, insider, analysts, social, fundamentals, predictions
from app.db import init_db

app = FastAPI(title="SwingIntel API", version="0.2.0")

# Wide open for local dev. Tighten allow_origins to your actual frontend
# domain before deploying anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    # Safe to call even if DATABASE_URL isn't set yet — it just no-ops
    # and prediction logging/history will 503 with a clear message until
    # it is.
    init_db()


app.include_router(prices.router, prefix="/api/prices", tags=["prices"])
app.include_router(markets.router, prefix="/api/markets", tags=["markets"])
app.include_router(news.router, prefix="/api/news", tags=["news"])
app.include_router(insider.router, prefix="/api/insider", tags=["insider"])
app.include_router(analysts.router, prefix="/api/analysts", tags=["analysts"])
app.include_router(social.router, prefix="/api/social", tags=["social"])
app.include_router(fundamentals.router, prefix="/api/fundamentals", tags=["fundamentals"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
