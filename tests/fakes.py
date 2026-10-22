"""Test doubles for injected dependencies."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

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


class TaskAwareProvider:
    """An LLM provider that answers by task, for long-running browser tests.

    Each answer is built from the last message sent (the task's JSON payload), so one
    provider can serve any number of requests in any order.
    """

    def __init__(
        self, name: str, answers: Mapping[type[BaseModel], Callable[[str], BaseModel]]
    ) -> None:
        self._name = name
        self._answers = answers

    @property
    def name(self) -> str:
        return self._name

    def generate(self, request: LLMRequest) -> str:
        answer = self._answers[request.schema]
        return answer(request.messages[-1][1]).model_dump_json()
