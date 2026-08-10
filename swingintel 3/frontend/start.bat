@echo off
cd /d "%~dp0"

echo Installing dependencies (first run only takes a bit longer)...
call npm install --silent

echo.
echo Starting the app — it'll open at http://localhost:5173
echo.
call npm run dev
