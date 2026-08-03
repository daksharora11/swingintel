@echo off
cd /d "%~dp0"

if not exist ".env" (
  copy .env.example .env >nul
  echo Created .env — open it and add:
  echo   NEWSAPI_KEY from https://newsapi.org/register (for the news Refresh button)
  echo   FINNHUB_KEY from https://finnhub.io/register (for Insider + Analyst factors)
  echo   DATABASE_URL from https://neon.tech, a free Postgres project (for prediction history)
  echo (Prices, fundamentals, short interest, Reddit buzz, and markets all work without any of these.)
  echo.
)

echo Installing dependencies (first run only takes a bit longer)...
pip install -r requirements.txt --quiet

echo.
echo Starting backend at http://localhost:8000 — leave this window open.
echo Check it's alive any time at http://localhost:8000/api/health
echo.
uvicorn app.main:app --reload --port 8000
