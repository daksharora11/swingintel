"""
Prediction history storage — Postgres via psycopg2.

Deliberately NOT using Render's free Postgres: it hard-deletes after 30
days, which would wipe out exactly the 30-day comparison this is for.
Neon (neon.tech) has a genuinely permanent free tier — no credit card,
no expiry, just a connection string. Any standard Postgres connection
string works here, so Supabase or another provider is a drop-in swap.

Needs DATABASE_URL in the environment, e.g.:
    postgresql://user:password@ep-xxxx.neon.tech/neondb?sslmode=require
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager


def get_database_url():
    return os.environ.get("DATABASE_URL")


@contextmanager
def get_connection():
    url = get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL not set in environment")
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create the predictions table if it doesn't exist yet. Safe to call
    on every startup — CREATE TABLE IF NOT EXISTS is idempotent."""
    url = get_database_url()
    if not url:
        # No DB configured — predictions logging/history will just 503
        # until DATABASE_URL is set. Everything else in the app works fine
        # without it.
        return False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    predicted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    composite_score REAL NOT NULL,
                    news_score REAL,
                    event_score REAL,
                    momentum_score REAL,
                    insider_score REAL,
                    analyst_score REAL,
                    fundamentals_score REAL,
                    price_at_prediction REAL NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_predictions_ticker_time ON predictions (ticker, predicted_at)"
            )
    return True
