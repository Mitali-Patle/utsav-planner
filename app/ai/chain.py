"""Fallback chain: cache, then each provider in order, then a labelled non-AI fallback.

The chain never raises for provider problems. Rate-limited providers sit out until their
quota resets, a provider that fails twice in a row (for example, "model overloaded") rests
briefly, invalid answers get one repair attempt on the same model, and the whole call is
capped in time. After a timeout, other models from the same vendor are skipped for that
request: a slow vendor is usually slow for all its models, so the next vendor gets the time.
The result always says which provider (if any) produced it.
"""

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from app.ai.cache import CacheStore, cache_key
from app.ai.client import (
    AIResult,
    AITask,
    Attempt,
    AttemptOutcome,
    LLMRequest,
    Message,
    OutputRejectedError,
    Provenance,
    Provider,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
DEFAULT_RETRY_AFTER_S = 60.0
UNAVAILABLE_COOLDOWN_S = 30.0
FAILURES_BEFORE_COOLDOWN = 2
MAX_PROBLEMS = 5
MAX_ECHO_CHARS = 2_000
REPAIR_ATTEMPTS = 1


def next_quota_reset(now: datetime) -> datetime:
    """Next midnight in US Pacific time, when free-tier daily quotas reset.

    Examples:
        >>> next_quota_reset(datetime(2026, 10, 22, 6, 0, tzinfo=UTC)).isoformat()
        '2026-10-22T07:00:00+00:00'
    """
    local = now.astimezone(PACIFIC)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), PACIFIC)
    return midnight.astimezone(UTC)


@dataclass(frozen=True)
class ChainLimits:
    """Time limits, in seconds."""

    per_call_timeout_s: float = 10.0  # Gemini rejects server deadlines under 10 s
    total_budget_s: float = 25.0


@dataclass
class _Run[C, T: BaseModel]:
    """State of one `FallbackChain.run` call."""

    task: AITask[C, T]
    context: C
    payload: dict[str, Any]
    started: float
    deadline: float
    attempts: list[Attempt] = field(default_factory=list)
    slow_vendors: set[str] = field(default_factory=set)


class FallbackChain:
    """Runs AI tasks across providers with caching, cooldowns, repair and fallback."""

    def __init__(
        self,
        providers: Sequence[Provider],
        cache: CacheStore,
        limits: ChainLimits | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create a chain.

        Args:
            providers: Providers in order of preference.
            cache: Store for successful answers.
            limits: Per-call and total time limits.
            clock: Monotonic clock for timing (injectable for tests).
            now: Wall clock for quota cooldowns (injectable for tests).
        """
        self._providers = tuple(providers)
        self._cache = cache
        self._limits = limits or ChainLimits()
        self._clock = clock
        self._now = now
        self._cooldowns: dict[str, datetime] = {}
        self._failures: dict[str, int] = {}

    def run[C, T: BaseModel](
        self, task: AITask[C, T], context: C, *, use_cache: bool = True
    ) -> AIResult[T]:
        """Produce a validated answer for a task, falling back if every provider fails.

        Args:
            task: The kind of AI call.
            context: Data the task's payload builder, checks and fallback use.
            use_cache: Whether to reuse and store cached answers ("regenerate" sets False).

        Returns:
            The answer and its provenance.
        """
        payload = task.build_payload(context)
        key = cache_key(task.name, task.version, payload)
        if use_cache and (cached := self._cache.get(key)) is not None:
            return _from_cache(task, payload, cached)

        started = self._clock()
        run = _Run(task, context, payload, started, started + self._limits.total_budget_s)
        for provider in self._providers:
            skip = self._skip_reason(provider, run)
            if skip is not None:
                run.attempts.append(Attempt(provider=provider.name, outcome=skip, latency_ms=0))
                continue
            value = self._try_provider(provider, run)
            if value is not None:
                if use_cache:
                    entry = {"value": value.model_dump(mode="json"), "provider": provider.name}
                    self._cache.set(key, entry, task.cache_ttl_s)
                return AIResult(value, self._provenance(run, provider.name))
            if self._clock() >= run.deadline:
                break

        logger.warning("ai %s: all providers failed, using fallback", task.name)
        return AIResult(task.fallback(context), self._provenance(run, None, fallback=True))

    def offline[C, T: BaseModel](self, task: AITask[C, T], context: C) -> AIResult[T]:
        """The task's labelled fallback, without calling any provider.

        Used when a client is over its AI rate limit: the app keeps working, honestly labelled.
        """
        payload = task.build_payload(context)
        started = self._clock()
        run = _Run(task, context, payload, started, started)
        return AIResult(task.fallback(context), self._provenance(run, None, fallback=True))

    def _try_provider[C, T: BaseModel](self, provider: Provider, run: _Run[C, T]) -> T | None:
        """Call one provider, with one repair attempt for an invalid answer."""
        messages = _conversation(run.task, run.payload)
        repairs_left = REPAIR_ATTEMPTS
        while True:
            remaining = run.deadline - self._clock()
            if remaining <= 0:
                return None
            call_started = self._clock()
            outcome, raw = self._call(provider, self._request(run.task, messages, remaining))
            if outcome is not AttemptOutcome.OK:
                run.attempts.append(self._attempt(provider, outcome, call_started))
                if outcome is AttemptOutcome.TIMEOUT:
                    run.slow_vendors.add(_vendor(provider))
                return None
            try:
                value = run.task.check(run.task.output.model_validate_json(raw), run.context)
            except (ValidationError, OutputRejectedError) as error:
                run.attempts.append(self._attempt(provider, AttemptOutcome.INVALID, call_started))
                messages = (
                    *messages,
                    ("model", raw[:MAX_ECHO_CHARS]),
                    ("user", _repair_note(error)),
                )
                if repairs_left == 0:
                    return None
                repairs_left -= 1
                continue
            run.attempts.append(self._attempt(provider, AttemptOutcome.OK, call_started))
            logger.info("ai %s via %s ok", run.task.name, provider.name)
            return value

    def _request[C, T: BaseModel](
        self, task: AITask[C, T], messages: tuple[Message, ...], remaining_s: float
    ) -> LLMRequest:
        return LLMRequest(
            system=task.system,
            messages=messages,
            schema=task.output,
            temperature=task.temperature,
            max_output_tokens=task.max_output_tokens,
            timeout_s=min(self._limits.per_call_timeout_s, remaining_s),
        )

    def _call(self, provider: Provider, request: LLMRequest) -> tuple[AttemptOutcome, str]:
        try:
            raw = provider.generate(request)
        except RateLimitedError as error:
            self._cooldowns[provider.name] = (
                next_quota_reset(self._now())
                if error.daily
                else self._now() + timedelta(seconds=error.retry_after_s or DEFAULT_RETRY_AFTER_S)
            )
            logger.warning("ai provider %s rate limited (daily=%s)", provider.name, error.daily)
            return AttemptOutcome.RATE_LIMITED, ""
        except ProviderTimeoutError as error:
            logger.warning("ai provider %s timed out: %s", provider.name, error)
            return AttemptOutcome.TIMEOUT, ""
        except ProviderUnavailableError as error:
            failures = self._failures.get(provider.name, 0) + 1
            self._failures[provider.name] = failures
            if failures >= FAILURES_BEFORE_COOLDOWN:
                self._cooldowns[provider.name] = self._now() + timedelta(
                    seconds=UNAVAILABLE_COOLDOWN_S
                )
            logger.warning(
                "ai provider %s unavailable (%d in a row): %s", provider.name, failures, error
            )
            return AttemptOutcome.UNAVAILABLE, ""
        self._failures.pop(provider.name, None)
        return AttemptOutcome.OK, raw

    def _skip_reason[C, T: BaseModel](
        self, provider: Provider, run: _Run[C, T]
    ) -> AttemptOutcome | None:
        if self._cooling_down(provider.name):
            return AttemptOutcome.COOLING_DOWN
        if _vendor(provider) in run.slow_vendors:
            return AttemptOutcome.SKIPPED_AFTER_TIMEOUT
        return None

    def _cooling_down(self, provider_name: str) -> bool:
        until = self._cooldowns.get(provider_name)
        return until is not None and self._now() < until

    def _attempt(self, provider: Provider, outcome: AttemptOutcome, started: float) -> Attempt:
        return Attempt(
            provider=provider.name, outcome=outcome, latency_ms=self._elapsed_ms(started)
        )

    def _elapsed_ms(self, started: float) -> int:
        return round((self._clock() - started) * 1000)

    def _provenance[C, T: BaseModel](
        self, run: _Run[C, T], provider: str | None, *, fallback: bool = False
    ) -> Provenance:
        return Provenance(
            task=run.task.name,
            prompt_version=run.task.version,
            provider=provider,
            latency_ms=self._elapsed_ms(run.started),
            cached=False,
            fallback=fallback,
            attempts=tuple(run.attempts),
            request_payload=run.payload,
        )


def _vendor(provider: Provider) -> str:
    """Vendor part of a provider name, for example `gemini` in `gemini:gemini-3.5-flash-lite`."""
    return provider.name.partition(":")[0]


def _from_cache[C, T: BaseModel](
    task: AITask[C, T], payload: dict[str, Any], cached: Mapping[str, Any]
) -> AIResult[T]:
    provenance = Provenance(
        task=task.name,
        prompt_version=task.version,
        provider=cached["provider"],
        latency_ms=0,
        cached=True,
        fallback=False,
        attempts=(),
        request_payload=payload,
    )
    return AIResult(task.output.model_validate(cached["value"]), provenance)


def _conversation[C, T: BaseModel](
    task: AITask[C, T], payload: dict[str, Any]
) -> tuple[Message, ...]:
    """Few-shot examples as prior turns, then the real request, all as JSON."""
    messages: list[Message] = []
    for example_payload, example_answer in task.few_shots:
        messages.append(("user", json.dumps(example_payload, ensure_ascii=False)))
        messages.append(("model", example_answer.model_dump_json(exclude_none=True)))
    messages.append(("user", json.dumps(payload, ensure_ascii=False)))
    return tuple(messages)


def _repair_note(error: ValidationError | OutputRejectedError) -> str:
    if isinstance(error, OutputRejectedError):
        problems = error.problems
    else:
        problems = [
            f"{'.'.join(str(part) for part in item['loc']) or 'answer'}: {item['msg']}"
            for item in error.errors()
        ]
    listed = "; ".join(problems[:MAX_PROBLEMS])
    return f"Your previous answer was rejected: {listed}. Reply again with corrected JSON only."
