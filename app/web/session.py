"""Anonymous sessions: a random id, a CSRF token and one-time status messages.

The session is a signed cookie, so every visitor (including each judge) gets private data
without creating an account.
"""

import secrets
from typing import Literal

from fastapi import Request

from app.web.guard import CSRF_SESSION_KEY

SESSION_ID_KEY = "sid"
FLASH_KEY = "flash"
SESSION_MAX_AGE_S = 365 * 24 * 3600
"""A year, renewed on every visit: weddings are often planned many months ahead."""
FlashKind = Literal["info", "success", "error"]


def session_id(request: Request) -> str:
    """This visitor's session id, created on first use."""
    return _get_or_create(request, SESSION_ID_KEY)


def csrf_token(request: Request) -> str:
    """This visitor's CSRF token, created on first use."""
    return _get_or_create(request, CSRF_SESSION_KEY)


def flash(request: Request, message: str, kind: FlashKind = "info") -> None:
    """Queue a status message for the next page (used after redirect-after-POST)."""
    messages = list(request.session.get(FLASH_KEY, []))
    messages.append({"kind": kind, "message": message})
    request.session[FLASH_KEY] = messages


def pop_flashes(request: Request) -> list[dict[str, str]]:
    """Take the queued status messages, clearing them."""
    messages: list[dict[str, str]] = request.session.pop(FLASH_KEY, [])
    return messages


def _get_or_create(request: Request, key: str) -> str:
    value = request.session.get(key)
    if not isinstance(value, str):
        value = secrets.token_urlsafe(32)
        request.session[key] = value
    return value
