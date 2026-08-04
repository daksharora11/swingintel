# SwingIntel

Two ways to run this — pick one.

## Option A: Deploy online (no terminal needed) — recommended if the local setup was giving you trouble

You'll put the code on GitHub, then let two free hosting services (Render
for the backend, Vercel for the frontend) build and run it for you. No
Python or Node.js needs to be installed on your own computer for this path.

1. **Get a free GitHub account** at github.com if you don't have one.
2. **Install GitHub Desktop** (desktop.github.com) — a normal app, not the
   command line. Sign in with your GitHub account.
3. In GitHub Desktop: **File → Add Local Repository**, point it at this
   `swingintel` folder (it's already set up as a repo). Click **Publish
   repository** — pick a name, leave it public or private, done. Your code
   is now on GitHub.
4. **Deploy the backend**: go to render.com, sign up free, click **New +
   → Web Service**, connect your GitHub account, pick your `swingintel`
   repo. Set:
   - Root Directory: `backend`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

   (There's a `render.yaml` in this repo — Render may fill all of this in
   automatically if you use **New + → Blueprint** instead and just paste
   your repo. Try that first.)

   Under **Environment**, add `NEWSAPI_KEY` with your free key from
   newsapi.org, `FINNHUB_KEY` with a free key from finnhub.io (for the
   Insider and Analyst factors), and `DATABASE_URL` with a free Postgres
   connection string from neon.tech (for the prediction-history chart —
   NOT Render's own free Postgres, which auto-deletes after 30 days).
   Click **Create Web Service**. After it deploys, copy the URL it gives
   you (something like `https://swingintel-backend.onrender.com`).

5. **Deploy the frontend**: go to vercel.com, sign up free, **Add New →
   Project**, import the same GitHub repo. Set **Root Directory** to
   `frontend` (Vercel auto-detects it's a Vite app). Under **Environment
   Variables**, add `VITE_API_BASE` set to the Render URL from step 4.
   Click **Deploy**.

6. **Open the link Vercel gives you** — that's your live app, on the
   internet, no terminal ever needed.

Render's free tier sleeps after inactivity and takes ~30s to wake up on
the first request — normal for free hosting, not a bug.

## Option B: Run it locally instead

Needs Python 3 and Node.js installed on your machine.

**Terminal 1:**
```bash
cd backend
./start.sh
```
(Windows: double-click `start.bat`.) First run creates a `.env` file —
open it and add a free NewsAPI key from newsapi.org.

**Terminal 2:**
```bash
cd frontend
./start.sh
```
(Windows: double-click `start.bat`.)

Open http://localhost:5173 — that's the app. Backend health check:
http://localhost:8000/api/health.

### Troubleshooting local mode
- **"command not found"** → Python or Node.js isn't installed. Get them
  from python.org and nodejs.org, then open a *new* terminal window.
- **"permission denied"** on `./start.sh` → run `chmod +x start.sh` once.
- **Port already in use** → something else is using 8000 or 5173.

## What's live vs. still a demo

- **Prices** — real, via Yahoo Finance, polling every ~8s. No key needed.
- **News** — real once you click **Refresh** on a ticker (calls NewsAPI).
  It's a button, not automatic, because the free tier is ~100 requests/day.
- **Prediction Markets tab** — still curated demo data. Matching a real
  Polymarket market to "which ticker does this affect" is manual curation
  work, not something a fetch solves on its own.
- **Insider trading & Analyst ratings** — real, via Finnhub (needs
  `FINNHUB_KEY`). These feed directly into the composite gauge as
  weighted factors, same as News/Event/Momentum.
- **Company fundamentals** — real, via SEC EDGAR (10-K/10-Q revenue and
  net income growth), no key needed. Also a weighted composite factor.
- **Short interest & Reddit buzz** — real (short interest needs no key;
  Reddit uses the free ApeWisdom API, also no key). Shown as context
  badges on each prediction card rather than folded into the score —
  short interest cuts both ways (bearish positioning vs. squeeze setup),
  and Reddit mention volume measures attention, not direction.
- All five of the above update via a **Refresh** button per ticker card
  on the Live & Prediction tab, not automatically.
- **15 tracked tickers** across tech, auto, energy, financials, media,
  healthcare, retail, and industrials — not just the original 5.
- **Arbitrage cross-reference** — if a Polymarket mispricing flag from
  the Prediction Markets tab affects a specific ticker, that ticker's
  prediction card shows an amber banner linking to it.
- **Prediction history** — every time you hit Refresh on a ticker, its
  current composite score and price get logged. A chart on the Live &
  Prediction tab compares that logged score against what the price
  actually did over 1/7/30 days. Needs `DATABASE_URL`; history only
  accumulates from actual usage, not a guaranteed daily snapshot — true
  unattended daily logging would need a scheduled job (e.g. a Render Cron
  Job hitting `/api/predictions/log` once a day), which isn't set up here.
- **Trade signal notifications** — a bell icon in the header. Fires a
  Call (bullish) or Put (bearish) signal only when the composite score
  crosses an adjustable threshold AND a minimum number of the six factors
  agree on direction — the agreement requirement exists specifically so
  one loud factor can't fire a signal alone. Both numbers are adjustable
  in the panel. Fires once per direction change, not repeatedly. Can
  optionally enable real browser notifications. This is a rules-based
  signal from your own configured factors and thresholds, not investment
  advice — there's no backtested edge behind the default numbers.
- **Paper trading & accuracy tracking** — a Paper Trades tab. Open a
  simulated Call or Put from any ticker card or straight from a fired
  signal; close it manually whenever you decide. Entry and exit prices
  always come from a fresh Yahoo Finance call at the moment of the
  action — never estimated, never reused from a cache. The stats panel
  breaks win rate down by Call vs. Put specifically, which is the actual
  answer to "how good is this at predicting bullish vs. bearish moves."
  Needs `DATABASE_URL` (same database as prediction history).

Both the backend and frontend were built, linted, and run end-to-end
before being packaged — see `backend/README.md` for what was specifically
verified there.
