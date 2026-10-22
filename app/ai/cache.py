"""Cache for AI answers, keyed by task, prompt version and the exact request payload."""

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, Protocol


class CacheStore(Protocol):
    """Where cached AI answers live (in memory now, Postgres later)."""

    def get(self, key: str) -> Mapping[str, Any] | None:
        """Return a cached entry, or None if missing or expired."""
        ...

    def set(self, key: str, value: Mapping[str, Any], ttl_s: int) -> None:
        """Store an entry for `ttl_s` seconds."""
        ...


def cache_key(task: str, version: str, payload: Mapping[str, Any]) -> str:
    """Stable SHA-256 key for an AI request.

    Keys ignore dict ordering, so the same request always maps to the same entry.

    Examples:
        >>> cache_key("t", "v1", {"b": 1, "a": 2}) == cache_key("t", "v1", {"a": 2, "b": 1})
        True
        >>> cache_key("t", "v1", {"a": 1}) == cache_key("t", "v2", {"a": 1})
        False
    """
    canonical = json.dumps(
        [task, version, payload],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryCache:
    """Least-recently-used in-memory cache with per-entry expiry.

    Lives as long as a function instance; enough to absorb repeated requests and retries.
    """

    def __init__(self, max_entries: int = 512, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an empty cache.

        Args:
            max_entries: Oldest entries are dropped beyond this size.
            clock: Monotonic clock in seconds (injectable for tests).
        """
        self._entries: OrderedDict[str, tuple[float, Mapping[str, Any]]] = OrderedDict()
        self._max_entries = max_entries
        self._clock = clock

    def get(self, key: str) -> Mapping[str, Any] | None:
        """Return a live entry and mark it as recently used."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: Mapping[str, Any], ttl_s: int) -> None:
        """Store an entry, evicting the least recently used one if full."""
        self._entries[key] = (self._clock() + ttl_s, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class TieredCache:
    """Fast per-instance cache in front of a shared store.

    A hit in the shared store is copied into the fast cache for `refill_ttl_s` seconds.
    """

    def __init__(self, fast: CacheStore, shared: CacheStore, refill_ttl_s: int = 300) -> None:
        """Combine two stores.

        Args:
            fast: Checked first (usually `MemoryCache`).
            shared: Checked on a miss (usually the Postgres store).
            refill_ttl_s: How long a shared hit stays in the fast cache.
        """
        self._fast = fast
        self._shared = shared
        self._refill_ttl_s = refill_ttl_s

    def get(self, key: str) -> Mapping[str, Any] | None:
        """Return the entry from the fast cache, else from the shared store."""
        value = self._fast.get(key)
        if value is not None:
            return value
        value = self._shared.get(key)
        if value is not None:
            self._fast.set(key, value, self._refill_ttl_s)
        return value

    def set(self, key: str, value: Mapping[str, Any], ttl_s: int) -> None:
        """Store the entry in both caches."""
        self._fast.set(key, value, ttl_s)
        self._shared.set(key, value, ttl_s)
