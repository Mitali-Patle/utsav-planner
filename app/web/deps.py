"""Request dependencies shared by route modules."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine


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
