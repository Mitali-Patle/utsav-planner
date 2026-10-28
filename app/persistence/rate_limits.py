"""Per-client request counters for the AI and event-creation limits."""

from collections.abc import Sequence
from datetime import timedelta
from typing import NamedTuple

from sqlalchemy import Engine, delete

from app.persistence.common import Now, upsert, utc_now
from app.persistence.tables import rate_limits

PURGE_EVERY_WRITES = 50


RATE_LIMIT_RETENTION = timedelta(days=2)


class Counter(NamedTuple):
    """One fixed-window limit: at most `limit` requests per `window_s` in `bucket`."""

    bucket: str
    limit: int
    window_s: int


class RateLimiter:
    """Fixed-window counters shared across function instances."""

    def __init__(self, engine: Engine, now: Now = utc_now) -> None:
        """Create the limiter."""
        self._engine = engine
        self._now = now
        self._writes = 0

    def hit(self, bucket: str, *, limit: int, window_s: int) -> bool:
        """Count one request in the current window.

        Args:
            bucket: Counter name, for example a hashed client address.
            limit: Requests allowed per window.
            window_s: Window length in seconds.

        Returns:
            True if the request is within the limit.
        """
        return self.hit_all([Counter(bucket, limit, window_s)])

    def hit_all(self, counters: Sequence[Counter]) -> bool:
        """Count one request against several counters, in order, in one transaction.

        Counting stops at the first counter over its limit, so a refused request is not
        charged to the counters after it (for example, the app-wide daily total).

        Returns:
            True if the request is within every limit.
        """
        moment = self._now()
        with self._engine.begin() as connection:
            within = True
            for counter in counters:
                window_start = int(moment.timestamp()) // counter.window_s * counter.window_s
                statement = upsert(
                    self._engine,
                    rate_limits,
                    {"bucket": counter.bucket, "window_start": window_start, "count": 1},
                    keys=("bucket", "window_start"),
                    increment="count",
                ).returning(rate_limits.c.count)
                if connection.execute(statement).scalar_one() > counter.limit:
                    within = False
                    break
            self._writes += 1
            if self._writes % PURGE_EVERY_WRITES == 0:
                oldest = int((moment - RATE_LIMIT_RETENTION).timestamp())
                connection.execute(delete(rate_limits).where(rate_limits.c.window_start < oldest))
        return within
