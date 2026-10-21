"""Security headers added to every response the app generates."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "form-action 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    ]
)

BASE_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS_HEADER = ("Strict-Transport-Security", "max-age=63072000; includeSubDomains")


class SecurityHeadersMiddleware:
    """Pure ASGI middleware that sets security headers without buffering the response."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        """Wrap an ASGI app.

        Args:
            app: The application to wrap.
            hsts: Whether to send Strict-Transport-Security (production only).
        """
        self.app = app
        self.headers = dict(BASE_HEADERS)
        if hsts:
            self.headers[HSTS_HEADER[0]] = HSTS_HEADER[1]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI connection, adding headers to HTTP responses."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
