"""AI rate limits that degrade gracefully instead of blocking.

Over the limit, a request still works: personalization uses the labelled fallback and change
requests offer the structured form. Client addresses are stored only as keyed hashes.
"""

import hashlib
import hmac
import logging
from typing import Protocol

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings

logger = logging.getLogger(__name__)

IP_WINDOW_S = 600
DAY_S = 86_400
GLOBAL_BUCKET = "ai:global"


class CounterStore(Protocol):
    """Shared request counters (implemented by `persistence.RateLimiter`)."""

    def hit(self, bucket: str, *, limit: int, window_s: int) -> bool:
        """Count one request; True if it is within the limit."""
        ...


class AIRateLimit:
    """Per-client and global daily limits on AI calls."""

    def __init__(self, limiter: CounterStore | None, settings: Settings, secret: bytes) -> None:
        """Create the limit.

        Args:
            limiter: Shared counters, or None (no database) to allow everything.
            settings: Provides the limits.
            secret: Key for hashing client addresses.
        """
        self._limiter = limiter
        self._per_client = settings.ai_rate_limit_per_10min
        self._daily = settings.ai_daily_soft_cap
        self._secret = secret

    def allow(self, request: Request) -> bool:
        """Count this AI request; False if the client or the whole app is over its limit.

        A database error never blocks the AI: the request is allowed and the error logged.
        """
        if self._limiter is None:
            return True
        bucket = (
            "ai:ip:"
            + hmac.new(self._secret, client_ip(request).encode(), hashlib.sha256).hexdigest()[:32]
        )
        try:
            return self._limiter.hit(
                bucket, limit=self._per_client, window_s=IP_WINDOW_S
            ) and self._limiter.hit(GLOBAL_BUCKET, limit=self._daily, window_s=DAY_S)
        except SQLAlchemyError as error:
            logger.warning(
                "rate limiter unavailable (%s); allowing the request", type(error).__name__
            )
            return True


def client_ip(request: Request) -> str:
    """The client address: the first `X-Forwarded-For` entry (set by Vercel), else the peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"
