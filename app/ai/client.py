"""Contracts between the app and LLM providers.

A `Provider` turns an `LLMRequest` into raw JSON text or raises a typed `ProviderError`.
An `AITask` bundles everything one kind of AI call needs: prompt, payload builder, output
schema, checks against the plan, and a deterministic fallback. Every result carries
`Provenance`, so the UI can show exactly how it was produced.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

Message = tuple[str, str]  # (role, text); role is "user" or "model"


class ProviderError(Exception):
    """A provider could not return a usable response."""


class ProviderTimeoutError(ProviderError):
    """The provider did not answer within the time limit."""


class ProviderUnavailableError(ProviderError):
    """Network failure, server error, rejected request or empty answer."""


class RateLimitedError(ProviderError):
    """The provider's quota is used up for now.

    Attributes:
        daily: Whether the daily quota (rather than a per-minute limit) is exhausted.
        retry_after_s: Seconds the provider asked us to wait, if it said.
    """

    def __init__(self, message: str, *, daily: bool, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.daily = daily
        self.retry_after_s = retry_after_s


class OutputRejectedError(Exception):
    """Well-formed output that breaks a rule of the plan, such as an unknown id.

    Attributes:
        problems: Short descriptions, sent back to the model for one repair attempt.
    """

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class LLMRequest:
    """One call to a model."""

    system: str
    messages: tuple[Message, ...]
    schema: type[BaseModel]
    temperature: float
    max_output_tokens: int
    timeout_s: float


class Provider(Protocol):
    """Anything that can answer an `LLMRequest` with JSON text."""

    @property
    def name(self) -> str:
        """Identifier such as `gemini:gemini-3.5-flash-lite`."""
        ...

    def generate(self, request: LLMRequest) -> str:
        """Return the model's raw JSON answer.

        Raises:
            ProviderError: A typed failure the chain knows how to handle.
        """
        ...


@runtime_checkable
class Closable(Protocol):
    """A provider that holds a network client to release when the app shuts down."""

    def close(self) -> None:
        """Release the client."""
        ...


@dataclass(frozen=True)
class AITask[C, T: BaseModel]:
    """Everything one kind of AI call needs.

    Attributes:
        name: Task name, also used in cache keys and provenance.
        version: Prompt version; changing it invalidates cached answers.
        system: System prompt.
        output: Pydantic model the answer must match.
        temperature: Sampling temperature.
        max_output_tokens: Upper bound on answer length.
        cache_ttl_s: How long a successful answer may be reused.
        build_payload: Turns the context into the JSON user message.
        few_shots: Example (payload, answer) pairs sent before the real request.
        check: Validates an answer against the context. It may drop invalid parts and
            return a cleaned answer, or raise `OutputRejectedError`.
        fallback: Deterministic, non-AI answer used when every provider fails.
    """

    name: str
    version: str
    system: str
    output: type[T]
    temperature: float
    max_output_tokens: int
    cache_ttl_s: int
    build_payload: Callable[[C], dict[str, Any]]
    few_shots: tuple[tuple[Mapping[str, Any], T], ...]
    check: Callable[[T, C], T]
    fallback: Callable[[C], T]


class AttemptOutcome(StrEnum):
    """What happened when a provider was tried."""

    OK = "ok"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    COOLING_DOWN = "cooling_down"
    SKIPPED_AFTER_TIMEOUT = "skipped_after_timeout"


class Attempt(BaseModel):
    """One try against one provider."""

    model_config = ConfigDict(frozen=True)

    provider: str
    outcome: AttemptOutcome
    latency_ms: int


class Provenance(BaseModel):
    """How a result was produced, for the "How this was generated" panel."""

    model_config = ConfigDict(frozen=True)

    task: str
    prompt_version: str
    provider: str | None
    latency_ms: int
    cached: bool
    fallback: bool
    attempts: tuple[Attempt, ...]
    request_payload: dict[str, Any]


@dataclass(frozen=True)
class AIResult[T: BaseModel]:
    """A validated answer and its provenance."""

    value: T
    provenance: Provenance
