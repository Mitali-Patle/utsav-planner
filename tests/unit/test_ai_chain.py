"""Fallback chain: every failure path ends in a valid, honestly labelled answer."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel

from app.ai.cache import MemoryCache
from app.ai.chain import ChainLimits, FallbackChain, next_quota_reset
from app.ai.client import (
    AITask,
    AttemptOutcome,
    OutputRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)
from tests.fakes import FakeClock, ScriptedProvider

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)  # 23:00 the previous day in US Pacific


class Echo(BaseModel):
    answer: str
    n: int


def check(value: Echo, context: str) -> Echo:
    if value.n < 0:
        raise OutputRejectedError([f"n must not be negative for {context}"])
    return value


TASK: AITask[str, Echo] = AITask(
    name="echo",
    version="v1",
    system="You echo.",
    output=Echo,
    temperature=0.2,
    max_output_tokens=100,
    cache_ttl_s=600,
    build_payload=lambda context: {"task": "echo", "question": context},
    few_shots=(({"task": "echo", "question": "example"}, Echo(answer="example", n=1)),),
    check=check,
    fallback=lambda context: Echo(answer=f"fallback for {context}", n=0),
)

GOOD = json.dumps({"answer": "hi", "n": 2})


class Clocks:
    def __init__(self) -> None:
        self.monotonic = FakeClock()
        self.wall = NOW

    def now(self) -> datetime:
        return self.wall


def make_chain(
    *providers: ScriptedProvider, clocks: Clocks | None = None, **limits: Any
) -> FallbackChain:
    clocks = clocks or Clocks()
    return FallbackChain(
        providers,
        MemoryCache(clock=clocks.monotonic),
        ChainLimits(**limits),
        clock=clocks.monotonic,
        now=clocks.now,
    )


def outcomes(result: Any) -> list[tuple[str, AttemptOutcome]]:
    return [(a.provider, a.outcome) for a in result.provenance.attempts]


def test_first_provider_answers() -> None:
    primary = ScriptedProvider("primary", [GOOD])
    result = make_chain(primary).run(TASK, "hello")
    assert result.value == Echo(answer="hi", n=2)
    provenance = result.provenance
    assert (provenance.provider, provenance.fallback, provenance.cached) == (
        "primary",
        False,
        False,
    )
    assert (provenance.task, provenance.prompt_version) == ("echo", "v1")
    assert provenance.request_payload == {"task": "echo", "question": "hello"}
    request = primary.requests[0]
    assert request.system == "You echo."
    assert request.schema is Echo
    assert [role for role, _ in request.messages] == ["user", "model", "user"]
    assert json.loads(request.messages[-1][1]) == {"task": "echo", "question": "hello"}


def test_repeated_request_is_served_from_cache() -> None:
    primary = ScriptedProvider("primary", [GOOD])
    chain = make_chain(primary)
    chain.run(TASK, "hello")
    cached = chain.run(TASK, "hello")
    assert cached.value == Echo(answer="hi", n=2)
    assert (cached.provenance.cached, cached.provenance.provider) == (True, "primary")
    assert len(primary.requests) == 1


def test_cache_can_be_bypassed_for_regeneration() -> None:
    primary = ScriptedProvider("primary", [GOOD, json.dumps({"answer": "new", "n": 3})])
    chain = make_chain(primary)
    chain.run(TASK, "hello")
    assert chain.run(TASK, "hello", use_cache=False).value.answer == "new"


def test_timeout_moves_to_the_next_provider() -> None:
    primary = ScriptedProvider("primary", [ProviderTimeoutError("slow")])
    backup = ScriptedProvider("backup", [GOOD])
    result = make_chain(primary, backup).run(TASK, "hello")
    assert result.provenance.provider == "backup"
    assert outcomes(result) == [("primary", AttemptOutcome.TIMEOUT), ("backup", AttemptOutcome.OK)]


def test_daily_quota_benches_the_provider_until_pacific_midnight() -> None:
    clocks = Clocks()
    primary = ScriptedProvider("primary", [RateLimitedError("quota", daily=True), GOOD])
    backup = ScriptedProvider("backup", [GOOD, GOOD])
    chain = make_chain(primary, backup, clocks=clocks)
    chain.run(TASK, "first")
    second = chain.run(TASK, "second")
    assert outcomes(second)[0] == ("primary", AttemptOutcome.COOLING_DOWN)
    clocks.wall = next_quota_reset(NOW)
    assert chain.run(TASK, "third").provenance.provider == "primary"


def test_per_minute_limit_uses_the_retry_delay() -> None:
    clocks = Clocks()
    primary = ScriptedProvider(
        "primary", [RateLimitedError("rpm", daily=False, retry_after_s=20), GOOD]
    )
    backup = ScriptedProvider("backup", [GOOD, GOOD])
    chain = make_chain(primary, backup, clocks=clocks)
    chain.run(TASK, "first")
    clocks.wall = NOW + timedelta(seconds=10)
    assert chain.run(TASK, "second").provenance.provider == "backup"
    clocks.wall = NOW + timedelta(seconds=21)
    assert chain.run(TASK, "third").provenance.provider == "primary"


def test_unavailable_provider_cools_down_briefly() -> None:
    clocks = Clocks()
    primary = ScriptedProvider("primary", [ProviderUnavailableError("500"), GOOD])
    backup = ScriptedProvider("backup", [GOOD, GOOD])
    chain = make_chain(primary, backup, clocks=clocks)
    assert chain.run(TASK, "first").provenance.provider == "backup"
    assert chain.run(TASK, "second").provenance.provider == "backup"
    clocks.wall = NOW + timedelta(seconds=31)
    assert chain.run(TASK, "third").provenance.provider == "primary"


def test_invalid_json_gets_one_repair_on_the_same_model() -> None:
    primary = ScriptedProvider("primary", ["not json", GOOD])
    result = make_chain(primary).run(TASK, "hello")
    assert result.provenance.provider == "primary"
    assert outcomes(result) == [("primary", AttemptOutcome.INVALID), ("primary", AttemptOutcome.OK)]
    repair = primary.requests[1].messages
    assert repair[-2] == ("model", "not json")
    assert repair[-1][1].startswith("Your previous answer was rejected:")


def test_rule_violation_is_explained_in_the_repair_note() -> None:
    primary = ScriptedProvider("primary", [json.dumps({"answer": "x", "n": -1}), GOOD])
    make_chain(primary).run(TASK, "hello")
    assert "n must not be negative for hello" in primary.requests[1].messages[-1][1]


def test_failed_repair_moves_to_the_next_provider() -> None:
    primary = ScriptedProvider("primary", ["{}", "{}"])
    backup = ScriptedProvider("backup", [GOOD])
    result = make_chain(primary, backup).run(TASK, "hello")
    assert outcomes(result) == [
        ("primary", AttemptOutcome.INVALID),
        ("primary", AttemptOutcome.INVALID),
        ("backup", AttemptOutcome.OK),
    ]


def test_everything_failing_returns_the_labelled_fallback() -> None:
    primary = ScriptedProvider(
        "primary", [ProviderTimeoutError("slow"), ProviderTimeoutError("slow")]
    )
    backup = ScriptedProvider("backup", [RateLimitedError("quota", daily=True)])
    chain = make_chain(primary, backup)
    result = chain.run(TASK, "hello")
    assert result.value == Echo(answer="fallback for hello", n=0)
    assert (result.provenance.fallback, result.provenance.provider) == (True, None)
    again = chain.run(TASK, "hello")
    assert again.provenance.fallback, "fallbacks are never cached as AI answers"
    assert len(primary.requests) == 2, "a timeout does not bench the provider; AI is retried"
    assert outcomes(again)[-1] == ("backup", AttemptOutcome.COOLING_DOWN)


def test_total_time_budget_stops_the_chain() -> None:
    clocks = Clocks()
    slow = ScriptedProvider(
        "slow", [ProviderTimeoutError("slow")], clock=clocks.monotonic, latency_s=26
    )
    never = ScriptedProvider("never", [])
    result = make_chain(slow, never, clocks=clocks).run(TASK, "hello")
    assert result.provenance.fallback
    assert never.requests == []


def test_each_call_gets_the_smaller_of_its_limit_and_the_remaining_budget() -> None:
    clocks = Clocks()
    first = ScriptedProvider(
        "first", [ProviderTimeoutError("slow")], clock=clocks.monotonic, latency_s=20
    )
    second = ScriptedProvider("second", [GOOD])
    make_chain(first, second, clocks=clocks, per_call_timeout_s=12, total_budget_s=25).run(
        TASK, "hello"
    )
    assert first.requests[0].timeout_s == 12
    assert second.requests[0].timeout_s == pytest.approx(5)


def test_repair_is_skipped_when_time_runs_out() -> None:
    clocks = Clocks()
    primary = ScriptedProvider("primary", ["bad"], clock=clocks.monotonic, latency_s=30)
    result = make_chain(primary, clocks=clocks).run(TASK, "hello")
    assert result.provenance.fallback
    assert len(primary.requests) == 1


def test_quota_reset_is_midnight_pacific() -> None:
    assert next_quota_reset(NOW) == datetime(2026, 10, 22, 7, 0, tzinfo=UTC)
    after_reset = next_quota_reset(datetime(2026, 10, 22, 8, 0, tzinfo=UTC))
    assert after_reset == datetime(2026, 10, 23, 7, 0, tzinfo=UTC)


def test_memory_cache_expires_and_evicts() -> None:
    clock = FakeClock()
    cache = MemoryCache(max_entries=2, clock=clock)
    cache.set("a", {"v": 1}, ttl_s=10)
    cache.set("b", {"v": 2}, ttl_s=100)
    assert cache.get("a") == {"v": 1}
    cache.set("c", {"v": 3}, ttl_s=100)
    assert cache.get("b") is None, "least recently used entry is evicted"
    clock.advance(11)
    assert cache.get("a") is None, "expired entry is dropped"
    assert cache.get("c") == {"v": 3}


def test_default_per_call_limit_is_eight_seconds() -> None:
    primary = ScriptedProvider("gemini:a", [GOOD])
    make_chain(primary).run(TASK, "hello")
    assert primary.requests[0].timeout_s == 8


def test_timeout_skips_other_models_from_the_same_vendor() -> None:
    first = ScriptedProvider("gemini:a", [ProviderTimeoutError("slow")])
    second = ScriptedProvider("gemini:b", [])
    other = ScriptedProvider("groq:c", [GOOD])
    result = make_chain(first, second, other).run(TASK, "hello")
    assert result.provenance.provider == "groq:c"
    assert outcomes(result) == [
        ("gemini:a", AttemptOutcome.TIMEOUT),
        ("gemini:b", AttemptOutcome.SKIPPED_AFTER_TIMEOUT),
        ("groq:c", AttemptOutcome.OK),
    ]
    assert second.requests == []


def test_rate_limit_still_tries_the_next_model_from_the_same_vendor() -> None:
    first = ScriptedProvider("gemini:a", [RateLimitedError("quota", daily=True)])
    second = ScriptedProvider("gemini:b", [GOOD])
    result = make_chain(first, second).run(TASK, "hello")
    assert result.provenance.provider == "gemini:b"


def test_slow_vendor_is_only_skipped_for_that_request() -> None:
    first = ScriptedProvider("gemini:a", [ProviderTimeoutError("slow"), GOOD])
    other = ScriptedProvider("groq:c", [GOOD])
    chain = make_chain(first, other)
    chain.run(TASK, "first")
    assert chain.run(TASK, "second").provenance.provider == "gemini:a"
