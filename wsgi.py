"""WSGI entry point for Gunicorn / Render.

Run locally with Gunicorn:
    gunicorn wsgi:app
On Render (web service start command):
    gunicorn wsgi:app -w 2 -b 0.0.0.0:$PORT
"""
from app import create_app

app = create_app()