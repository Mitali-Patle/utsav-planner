"""Vercel entrypoint: Vercel imports the module-level `app` from here."""

from app.main import create_app

app = create_app()
