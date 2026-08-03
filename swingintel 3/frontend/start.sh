#!/bin/bash
# One-command frontend starter. Run this with:  ./start.sh
set -e
cd "$(dirname "$0")"

echo "Installing dependencies (first run only takes a bit longer)..."
npm install --silent

echo ""
echo "Starting the app — it'll open at http://localhost:5173"
echo ""
npm run dev
