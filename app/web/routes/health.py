"""Liveness and readiness endpoints.

`/healthz` never touches the database, so uptime pings cannot wake Neon or use its compute
hours. `/readyz` checks the database and is meant for manual and deploy checks.
"""

from fastapi import APIRouter, Response, status

from app.persistence.db import is_database_reachable
from app.web.deps import EngineDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Report that the process is up.

    Returns:
        A fixed status payload.
    """
    return {"status": "ok"}


@router.head("/healthz", include_in_schema=False)
def healthz_head() -> Response:
    """Answer uptime monitors that check with HEAD.

    Returns:
        An empty 200 response.
    """
    return Response()


@router.get("/readyz")
def readyz(engine: EngineDep, response: Response) -> dict[str, str]:
    """Report whether the database is reachable.

    The response never says why the database is unavailable, so configuration details
    are not exposed publicly.

    Args:
        engine: Database engine, or None when not configured.
        response: Response used to set the status code.

    Returns:
        `{"status": "ok"}` with 200, or `{"status": "unavailable"}` with 503.
    """
    if engine is None or not is_database_reachable(engine):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ok"}
