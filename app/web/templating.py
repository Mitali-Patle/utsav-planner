"""Jinja2 environment shared by all routes.

Autoescaping is always on, and undefined variables raise instead of rendering as empty,
so template mistakes fail loudly in tests rather than silently in production.
"""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.domain.money import format_inr
from app.web.session import csrf_token, pop_flashes

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

environment = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=StrictUndefined,
)
environment.filters["inr"] = format_inr


def common_context(request: Request) -> dict[str, Any]:
    """Values every page needs: the CSRF token and any queued status messages."""
    return {"csrf_token": csrf_token(request), "flashes": pop_flashes(request)}


templates = Jinja2Templates(env=environment, context_processors=[common_context])
