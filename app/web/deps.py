"""Request dependencies shared by route modules."""

from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Engine

from app.domain.clock import Clock
from app.services import Services
from app.web.limits import AIRateLimit, EventCreationLimit
from app.web.session import session_id


def get_engine(request: Request) -> Engine | None:
    """Return the application's database engine.

    Args:
        request: Incoming request.

    Returns:
        The engine, or None when no database is configured.
    """
    engine: Engine | None = request.app.state.engine
    return engine


EngineDep = Annotated[Engine | None, Depends(get_engine)]


def get_services(request: Request) -> Services:
    """Return the wired services.

    Raises:
        HTTPException: 503 when the app runs without a database.
    """
    services: Services | None = request.app.state.services
    if services is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The planner is not available right now."
        )
    return services


def get_ai_limit(request: Request) -> AIRateLimit:
    """Return the app's AI rate limit."""
    limit: AIRateLimit = request.app.state.ai_limit
    return limit


def get_create_limit(request: Request) -> EventCreationLimit:
    """Return the app's limit on creating events."""
    limit: EventCreationLimit = request.app.state.create_limit
    return limit


def get_today(request: Request) -> date:
    """Today's date in India, from the app's clock."""
    clock: Clock = request.app.state.clock
    return clock.today()


def get_session_id(request: Request) -> str:
    """This visitor's anonymous session id."""
    return session_id(request)


ServicesDep = Annotated[Services, Depends(get_services)]
TodayDep = Annotated[date, Depends(get_today)]
SessionDep = Annotated[str, Depends(get_session_id)]
AILimitDep = Annotated[AIRateLimit, Depends(get_ai_limit)]
CreateLimitDep = Annotated[EventCreationLimit, Depends(get_create_limit)]


@dataclass(frozen=True)
class Visit:
    """One request's context: the request itself, the services, who is asking and today."""

    request: Request
    services: Services
    session: str
    today: date


def get_visit(
    request: Request, services: ServicesDep, session: SessionDep, today: TodayDep
) -> Visit:
    """Bundle what every plan route needs, so routes take only their own inputs."""
    return Visit(request=request, services=services, session=session, today=today)


VisitDep = Annotated[Visit, Depends(get_visit)]
