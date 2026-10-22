"""Source of "today", injected so that date logic stays deterministic and testable."""

from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

INDIA_TZ = ZoneInfo("Asia/Kolkata")


class Clock(Protocol):
    """Provides the current date."""

    def today(self) -> date:
        """Return today's date."""
        ...


class SystemClock:
    """Clock backed by the system time in India (Asia/Kolkata)."""

    def today(self) -> date:
        """Return today's date in India.

        Returns:
            The current date in Asia/Kolkata.
        """
        return datetime.now(INDIA_TZ).date()
