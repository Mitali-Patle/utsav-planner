"""Test doubles for injected dependencies."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.ai.client import LLMRequest


@dataclass(frozen=True)
class FixedClock:
    """A clock that always returns the same day."""

    day: date

    def today(self) -> date:
        return self.day


class FakeClock:
    """A monotonic clock that only moves when told to."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedProvider:
    """An LLM provider that returns or raises scripted outcomes, in order.

    Each call can advance a fake clock to simulate latency.
    """

    def __init__(
        self,
        name: str,
        script: Sequence[str | Exception],
        *,
        clock: FakeClock | None = None,
        latency_s: float = 0.0,
    ) -> None:
        self._name = name
        self._script = list(script)
        self._clock = clock
        self._latency_s = latency_s
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def generate(self, request: LLMRequest) -> str:
        self.requests.append(request)
        if self._clock is not None:
            self._clock.advance(self._latency_s)
        if not self._script:
            raise AssertionError(f"{self._name} was called more times than scripted")
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
