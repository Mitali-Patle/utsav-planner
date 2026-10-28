"""AI rate limits that degrade gracefully instead of blocking.

Over the limit, a request still works: personalization uses the labelled fallback and change
requests offer the structured form. Client addresses are stored only as keyed hashes.
"""

import hashlib
import hmac
import logging
from collections.abc import Sequence
from typing import Protocol

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.persistence.rate_limits import Counter

logger = logging.getLogger(__name__)

IP_WINDOW_S = 600
HOUR_S = 3_600
DAY_S = 86_400
GLOBAL_BUCKET = "ai:global"


class CounterStore(Protocol):
    """Shared request counters (implemented by `persistence.RateLimiter`)."""

    def hit_all(self, counters: Sequence[Counter]) -> bool:
        """Count one request against each counter in order; True if within every limit."""
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
        bucket = client_bucket("ai", request, self._secret)
        try:
            return self._limiter.hit_all(
                [
                    Counter(bucket, self._per_client, IP_WINDOW_S),
                    Counter(GLOBAL_BUCKET, self._daily, DAY_S),
                ]
            )
        except SQLAlchemyError as error:
            logger.warning(
                "rate limiter unavailable (%s); allowing the request", type(error).__name__
            )
            return True


class EventCreationLimit:
    """At most a few new events per client address per hour.

    Creating events is the one way to grow storage without bound (every new browser session
    can create its own), so it is limited; edits stay within a plan's own bounds.
    """

    def __init__(self, limiter: CounterStore | None, settings: Settings, secret: bytes) -> None:
        """Create the limit.

        Args:
            limiter: Shared counters, or None (no database) to allow everything.
            settings: Provides the limit.
            secret: Key for hashing client addresses.
        """
        self._limiter = limiter
        self._per_hour = settings.event_create_limit_per_hour
        self._secret = secret

    def allow(self, request: Request) -> bool:
        """Count one new event; False if this client has created too many this hour.

        A database error never blocks: the event is allowed and the error logged.
        """
        if self._limiter is None:
            return True
        bucket = client_bucket("create", request, self._secret)
        try:
            return self._limiter.hit_all([Counter(bucket, self._per_hour, HOUR_S)])
        except SQLAlchemyError as error:
            logger.warning(
                "rate limiter unavailable (%s); allowing the request", type(error).__name__
            )
            return True


def client_bucket(prefix: str, request: Request, secret: bytes) -> str:
    """A counter name for this client: the address is HMAC-hashed, never stored as is."""
    digest = hmac.new(secret, client_ip(request).encode(), hashlib.sha256).hexdigest()[:32]
    return f"{prefix}:ip:{digest}"


def client_ip(request: Request) -> str:
    """The client address: the first `X-Forwarded-For` entry (set by Vercel), else the peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"
