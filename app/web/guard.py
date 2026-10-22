"""Request guard: body-size limit and CSRF check for every state-changing request.

Done once, as middleware, so no route can forget it. The token lives in the signed session
and must come back either in the `X-CSRF-Token` header (htmx) or in a `csrf_token` form
field (plain HTML forms).
"""

import secrets
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 32 * 1024
CSRF_SESSION_KEY = "csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
HTTP_FORBIDDEN = 403
HTTP_TOO_LARGE = 413


class RequestGuardMiddleware:
    """Rejects oversized bodies and requests without a valid CSRF token."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        """Wrap an ASGI app. Must run inside the session middleware.

        Args:
            app: The application to wrap.
            max_body_bytes: Largest request body accepted.
        """
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check unsafe requests, then replay the buffered body to the app."""
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        body = await self._read_body(headers, receive)
        if body is None:
            await _reject(HTTP_TOO_LARGE, "That request is too large.", scope, receive, send)
            return
        if not _csrf_ok(scope, headers, body):
            await _reject(
                HTTP_FORBIDDEN,
                "This form has expired. Please reload the page and try again.",
                scope,
                receive,
                send,
            )
            return

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _read_body(self, headers: Headers, receive: Receive) -> bytes | None:
        """Read the whole body, or return None as soon as it exceeds the limit."""
        declared = headers.get("content-length", "")
        if declared.isdecimal() and int(declared) > self.max_body_bytes:
            return None
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                return None
            chunks.append(chunk)
            more = message.get("more_body", False)
        return b"".join(chunks)


def _csrf_ok(scope: Scope, headers: Headers, body: bytes) -> bool:
    expected = scope.get("session", {}).get(CSRF_SESSION_KEY)
    supplied = headers.get(CSRF_HEADER)
    if supplied is None and headers.get("content-type", "").startswith(FORM_CONTENT_TYPE):
        values = parse_qs(body.decode("utf-8", errors="replace")).get(CSRF_FORM_FIELD, [])
        supplied = values[0] if values else None
    return (
        isinstance(expected, str)
        and isinstance(supplied, str)
        and secrets.compare_digest(expected.encode(), supplied.encode())
    )


async def _reject(status: int, message: str, scope: Scope, receive: Receive, send: Send) -> None:
    response = HTMLResponse(f'<p role="alert">{message}</p>', status_code=status)
    await response(scope, receive, send)
