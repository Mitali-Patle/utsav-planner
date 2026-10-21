"""Application factory: the single place where dependencies are wired together."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.web.routes import health

STATIC_DIR = Path(__file__).resolve().parent.parent / "public" / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Configuration to use. Defaults to settings read from the environment.

    Returns:
        The configured application.
    """
    settings = settings or get_settings()
    app = FastAPI(
        title="Utsav Planner",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.include_router(health.router)

    if settings.app_env == "development" and STATIC_DIR.is_dir():
        # In production Vercel serves public/ from its CDN; mount it only for local runs.
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
