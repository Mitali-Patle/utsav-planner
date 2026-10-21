"""Application factory: the single place where dependencies are wired together."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine

from app.config import Settings, get_settings
from app.persistence.db import make_engine
from app.web.routes import health, pages
from app.web.security import SecurityHeadersMiddleware

STATIC_DIR = Path(__file__).resolve().parent.parent / "public" / "static"


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Configuration to use. Defaults to settings read from the environment.
        engine: Database engine to use. Defaults to one built from `DATABASE_URL`, or none
            when no database is configured.

    Returns:
        The configured application.
    """
    settings = settings or get_settings()
    if engine is None and settings.database_url is not None:
        engine = make_engine(settings.database_url.get_secret_value())
    app = FastAPI(
        title="Utsav Planner",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.include_router(health.router)
    app.include_router(pages.router)

    if settings.app_env == "development" and STATIC_DIR.is_dir():
        # In production Vercel serves public/ from its CDN; mount it only for local runs.
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
