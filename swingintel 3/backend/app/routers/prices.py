"""
Live prices — backed by yfinance (pulls from Yahoo Finance, no API key).

For genuinely real-time streaming ticks rather than polling, swap this for
a paid feed (Polygon.io or Finnhub websockets) — yfinance is quote-delayed
and rate-limits under heavy polling, but it's the right starting point for
a project at this stage since it needs zero signup.
"""

from fastapi import APIRouter, HTTPException
import yfinance as yf

router = APIRouter()


@router.get("/{ticker}")
def get_quote(ticker: str):
    """Current price + day change for one ticker."""
    try:
        t = yf.Ticker(ticker.upper())
        info = t.fast_info
        price = info["last_price"]
        open_price = info["open"]
    except Exception as e:
        # yfinance raises on network/parsing failures rather than returning
        # empty data, so this is intentionally broad — any failure here
        # means "couldn't get a quote," which is what the caller needs to know.
        raise HTTPException(status_code=502, detail=f"Could not fetch quote for '{ticker}': {e}")

    change_pct = ((price - open_price) / open_price) * 100 if open_price else 0
    return {
        "ticker": ticker.upper(),
        "price": round(price, 2),
        "open": round(open_price, 2),
        "change_pct": round(change_pct, 2),
    }


@router.get("/{ticker}/history")
def get_history(ticker: str, period: str = "1d", interval: str = "5m"):
    """
    Intraday or daily history for the sparkline / momentum calc.
    period: e.g. '1d', '5d', '1mo'.  interval: e.g. '1m', '5m', '1d'.
    See yfinance docs for valid combinations — short intervals only work
    with short periods (Yahoo's own limit, not this API's).
    """
    try:
        t = yf.Ticker(ticker.upper())
        hist = t.history(period=period, interval=interval)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch history for '{ticker}': {e}")

    if hist.empty:
        raise HTTPException(status_code=404, detail=f"No history for '{ticker}'")

    return {
        "ticker": ticker.upper(),
        "points": [
            {"time": str(idx), "close": round(row["Close"], 2)}
            for idx, row in hist.iterrows()
        ],
    }


@router.get("")
def get_quotes_batch(tickers: str):
    """Batch quotes: /api/prices?tickers=NVDA,TSLA,AAPL"""
    symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
    out = {}
    for sym in symbols:
        try:
            out[sym] = get_quote(sym)
        except HTTPException:
            out[sym] = None
    return out


@router.get("/{ticker}/short-interest")
def get_short_interest(ticker: str):
    """
    Short interest — pulled from yfinance's info dict, which sources it
    from exchange-reported short interest (updated roughly twice a month,
    not real-time; that's a limitation of the underlying data itself, not
    this endpoint).
    """
    try:
        t = yf.Ticker(ticker.upper())
        info = t.get_info()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch short interest for '{ticker}': {e}")

    short_pct_float = info.get("shortPercentOfFloat")
    short_ratio = info.get("shortRatio")
    shares_short = info.get("sharesShort")
    date_short_interest = info.get("dateShortInterest")

    if short_pct_float is None and short_ratio is None:
        raise HTTPException(status_code=404, detail=f"No short interest data available for '{ticker}'")

    return {
        "ticker": ticker.upper(),
        "short_percent_of_float": round(short_pct_float * 100, 2) if short_pct_float is not None else None,
        "days_to_cover": short_ratio,
        "shares_short": shares_short,
        "as_of": date_short_interest,
    }
