"""Jinja2 environment shared by all routes.

Autoescaping is always on, and undefined variables raise instead of rendering as empty,
so template mistakes fail loudly in tests rather than silently in production.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

environment = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=StrictUndefined,
)
templates = Jinja2Templates(env=environment)
