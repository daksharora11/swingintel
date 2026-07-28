@echo off
cd /d "%~dp0"

if not exist ".env" (
  copy .env.example .env >nul
  echo Created .env — open it and add your free NewsAPI key from https://newsapi.org/register
  echo (Prices and markets will still work without it — only the news Refresh button needs it.)
  echo.
)

echo Installing dependencies (first run only takes a bit longer)...
pip install -r requirements.txt --quiet

echo.
echo Starting backend at http://localhost:8000 — leave this window open.
echo Check it's alive any time at http://localhost:8000/api/health
echo.
uvicorn app.main:app --reload --port 8000
