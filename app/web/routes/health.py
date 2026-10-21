"""Liveness endpoint.

It never touches the database, so uptime pings cannot wake Neon or use its compute hours.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Report that the process is up.

    Returns:
        A fixed status payload.
    """
    return {"status": "ok"}
