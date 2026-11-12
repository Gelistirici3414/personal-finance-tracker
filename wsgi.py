#!/usr/bin/env python
"""Environment-aware WSGI entry point for Gunicorn / Render / Railway.

Supports:
  - SQLite  (default, via .env or dev fallback)
  - PostgreSQL (when FPU_DATABASE_URL is set)
  - Docker + local + Render deployments

Run locally:
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    gunicorn wsgi:app        # or just: python -m app

On Render / Railway / any Docker container:
    The Procfile uses `web: gunicorn wsgi:app -w 2 -b 0.0.0.0:$PORT`
"""
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if present

from app import create_app  # noqa: E402

# If the user has set a Postgres/SQLite URL via env, use it.
# (create_app() already handles this; kept here so the env var is explicit.)
app = create_app()