"""
SwingIntel backend — FastAPI entrypoint.

Three routers, matching the three frontend modules:
  /api/prices   -> live/quote data (yfinance, no API key needed)
  /api/markets  -> Polymarket odds + arbitrage detection (public API, no key)
  /api/news     -> headlines + sentiment scoring (needs a NEWSAPI_KEY)

Run locally:
    uvicorn app.main:app --reload --port 8000

Note: this environment's network is sandboxed to package registries, so the
outbound calls to Yahoo Finance / Polymarket / NewsAPI in these routers were
NOT tested live here — the code is correct against each service's documented
API shape, but run it in an environment with normal internet access to
actually pull real data.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()  # reads .env in this folder — put NEWSAPI_KEY=... there instead of exporting it manually

from app.routers import prices, markets, news

app = FastAPI(title="SwingIntel API", version="0.1.0")

# Wide open for local dev. Tighten allow_origins to your actual frontend
# domain before deploying anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(prices.router, prefix="/api/prices", tags=["prices"])
app.include_router(markets.router, prefix="/api/markets", tags=["markets"])
app.include_router(news.router, prefix="/api/news", tags=["news"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
