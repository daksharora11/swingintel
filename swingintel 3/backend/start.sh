#!/bin/bash
# One-command backend starter. Run this with:  ./start.sh
set -e
cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env — open it and add your free NewsAPI key from https://newsapi.org/register"
  echo "(Prices and markets will still work without it — only the news Refresh button needs it.)"
  echo ""
fi

echo "Installing dependencies (first run only takes a bit longer)..."
pip install -r requirements.txt --quiet --break-system-packages 2>/dev/null || pip install -r requirements.txt --quiet

echo ""
echo "Starting backend at http://localhost:8000 — leave this window open."
echo "Check it's alive any time at http://localhost:8000/api/health"
echo ""
uvicorn app.main:app --reload --port 8000
