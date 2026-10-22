"""Application factory: the single place where dependencies are wired together."""

import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.domain.clock import Clock, SystemClock
from app.persistence.db import make_engine
from app.persistence.repositories import RateLimiter
from app.services import Services, build_services
from app.web.errors import register_error_handlers
from app.web.guard import RequestGuardMiddleware
from app.web.limits import AIRateLimit
from app.web.routes import changes, edits, events, health, pages
from app.web.security import SecurityHeadersMiddleware
from app.web.session import SESSION_MAX_AGE_S

STATIC_DIR = Path(__file__).resolve().parent.parent / "public" / "static"


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    services: Services | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Configuration to use. Defaults to settings read from the environment.
        engine: Database engine to use. Defaults to one built from `DATABASE_URL`, or none
            when no database is configured.
        services: Pre-built services (tests pass ones with a scripted model). Built from
            the engine when omitted; None when there is no database.
        clock: Source of today's date. Defaults to India time.

    Returns:
        The configured application.
    """
    settings = settings or get_settings()
    clock = clock or SystemClock()
    if engine is None and settings.database_url is not None:
        engine = make_engine(settings.database_url.get_secret_value())
    app = FastAPI(
        title="Utsav Planner",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    if services is None and engine is not None:
        services = build_services(settings, engine, clock)
    secret = _session_secret(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.services = services
    app.state.clock = clock
    app.state.ai_limit = AIRateLimit(
        RateLimiter(engine) if engine is not None else None, settings, secret.encode()
    )

    # Added inner-first: the guard needs the session, and headers apply to every response.
    app.add_middleware(RequestGuardMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="utsav_session",
        max_age=SESSION_MAX_AGE_S,
        same_site="lax",
        https_only=settings.is_production,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    register_error_handlers(app)
    for router in (health.router, pages.router, events.router, edits.router, changes.router):
        app.include_router(router)

    if settings.app_env == "development" and STATIC_DIR.is_dir():
        # In production Vercel serves public/ from its CDN; mount it only for local runs.
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _session_secret(settings: Settings) -> str:
    """The configured secret; outside production a random one (sessions reset on restart)."""
    if settings.session_secret is not None:
        return settings.session_secret.get_secret_value()
    return secrets.token_urlsafe(48)
