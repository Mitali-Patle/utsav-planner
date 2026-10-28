"""The AI answer cache stored in Postgres, shared by every serverless instance."""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, delete, select

from app.persistence.common import Now, upsert, utc_now
from app.persistence.rate_limits import PURGE_EVERY_WRITES
from app.persistence.tables import ai_cache


class SqlCacheStore:
    """AI answer cache in Postgres, shared by every function instance.

    Expired rows are removed every few writes, so the table stays small without a job.
    """

    def __init__(self, engine: Engine, now: Now = utc_now) -> None:
        """Create the store."""
        self._engine = engine
        self._now = now
        self._writes = 0

    def get(self, key: str) -> Mapping[str, Any] | None:
        """Return a live entry, or None."""
        with self._engine.connect() as connection:
            value: Mapping[str, Any] | None = connection.execute(
                select(ai_cache.c.value).where(
                    ai_cache.c.key == key, ai_cache.c.expires_at > self._now()
                )
            ).scalar_one_or_none()
        return value

    def set(self, key: str, value: Mapping[str, Any], ttl_s: int) -> None:
        """Insert or replace an entry."""
        moment = self._now()
        with self._engine.begin() as connection:
            connection.execute(
                upsert(
                    self._engine,
                    ai_cache,
                    {
                        "key": key,
                        "value": dict(value),
                        "expires_at": moment + timedelta(seconds=ttl_s),
                    },
                    keys=("key",),
                )
            )
            self._writes += 1
            if self._writes % PURGE_EVERY_WRITES == 0:
                connection.execute(delete(ai_cache).where(ai_cache.c.expires_at <= moment))
