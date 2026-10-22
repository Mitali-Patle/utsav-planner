"""Test doubles for injected dependencies."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FixedClock:
    """A clock that always returns the same day."""

    day: date

    def today(self) -> date:
        return self.day
