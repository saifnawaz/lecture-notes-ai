"""Vercel entry point. The FastAPI app serves both the API and the static frontend (static/)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # app.py, db.py, ... live one level up

import db  # noqa: E402
from app import app  # noqa: E402,F401  (Vercel looks for "app")

if not db.is_postgres():
    raise RuntimeError("DATABASE_URL is not set. On Vercel the disk is read-only, so Postgres is required.")
db.init_db()  # create missing tables once per cold start (safe to repeat)
