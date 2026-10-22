"""Provider adapters translate requests and every failure into the chain's types."""

import dataclasses
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors
from pydantic import BaseModel, SecretStr

from app.ai.client import (
    LLMRequest,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)
from app.ai.providers import build_providers
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.groq import GROQ_URL, GroqProvider
from app.config import Settings

KEY = SecretStr("test-key")


class Answer(BaseModel):
    ok: bool


REQUEST = LLMRequest(
    system="Be helpful.",
    messages=(("user", '{"q": 1}'), ("model", '{"ok": true}'), ("user", '{"q": 2}')),
    schema=Answer,
    temperature=0.1,
    max_output_tokens=300,
    timeout_s=12.0,
)


# Gemini


class FakeModels:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def gemini(outcome: object) -> tuple[GeminiProvider, FakeModels]:
    models = FakeModels(outcome)
    return GeminiProvider(
        "gemini-3.5-flash-lite", KEY, client=SimpleNamespace(models=models)
    ), models


def quota_error(quota_id: str, retry: str | None = None) -> errors.APIError:
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}],
        }
    ]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    return errors.APIError(
        429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    )


def test_gemini_maps_the_request() -> None:
    provider, models = gemini(SimpleNamespace(text='{"ok": true}'))
    assert provider.generate(REQUEST) == '{"ok": true}'
    assert provider.name == "gemini:gemini-3.5-flash-lite"
    call = models.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert [(c.role, c.parts[0].text) for c in call["contents"]] == list(REQUEST.messages)
    config = call["config"]
    assert (config.system_instruction, config.response_mime_type) == (
        "Be helpful.",
        "application/json",
    )
    assert config.response_schema is Answer
    assert (config.temperature, config.max_output_tokens) == (0.1, 300)
    assert config.http_options.timeout == 12_000


def test_gemini_never_sends_a_deadline_below_ten_seconds() -> None:
    """Regression: Gemini rejects deadlines under 10 s with 400 INVALID_ARGUMENT."""
    provider, models = gemini(SimpleNamespace(text='{"ok": true}'))
    provider.generate(dataclasses.replace(REQUEST, timeout_s=3.5))
    assert models.calls[0]["config"].http_options.timeout == 10_000


def test_gemini_daily_quota_with_retry_delay() -> None:
    provider, _ = gemini(quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "34s"))
    with pytest.raises(RateLimitedError) as error:
        provider.generate(REQUEST)
    assert (error.value.daily, error.value.retry_after_s) == (True, 34.0)


def test_gemini_per_minute_quota() -> None:
    provider, _ = gemini(quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))
    with pytest.raises(RateLimitedError) as error:
        provider.generate(REQUEST)
    assert (error.value.daily, error.value.retry_after_s) == (False, None)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (errors.APIError(504, {"error": {"code": 504}}), ProviderTimeoutError),
        (errors.APIError(500, {"error": {"code": 500}}), ProviderUnavailableError),
        (errors.APIError(400, {"error": {"code": 400}}), ProviderUnavailableError),
        (errors.APIError(429, "not json"), RateLimitedError),
        (httpx.ReadTimeout("slow"), ProviderTimeoutError),
        (httpx.ConnectError("down"), ProviderUnavailableError),
        (SimpleNamespace(text=""), ProviderUnavailableError),
        (SimpleNamespace(text=None), ProviderUnavailableError),
    ],
)
def test_gemini_failures_are_typed(outcome: object, expected: type[Exception]) -> None:
    provider, _ = gemini(outcome)
    with pytest.raises(expected):
        provider.generate(REQUEST)


def test_gemini_creates_its_sdk_client_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            created.append(api_key)
            self.models = FakeModels(SimpleNamespace(text='{"ok": true}'))

    monkeypatch.setattr("google.genai.Client", FakeClient)
    provider = GeminiProvider("gemini-3.5-flash-lite", KEY)
    assert created == []
    provider.generate(REQUEST)
    provider.generate(REQUEST)
    assert created == ["test-key"]


# Groq


def groq(
    handler: Callable[[httpx.Request], httpx.Response], model: str = "openai/gpt-oss-120b"
) -> GroqProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GroqProvider(model, KEY, http_client=client)


def answer(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_groq_maps_the_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer('{"ok": true}')

    provider = groq(handler)
    assert provider.generate(REQUEST) == '{"ok": true}'
    assert provider.name == "groq:openai/gpt-oss-120b"
    request = seen[0]
    assert str(request.url) == GROQ_URL
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == Answer.model_json_schema()
    assert (body["temperature"], body["max_completion_tokens"], body["reasoning_effort"]) == (
        0.1,
        300,
        "low",
    )


def test_groq_only_sends_reasoning_effort_to_reasoning_models() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return answer('{"ok": true}')

    groq(handler, model="llama-3.3-70b-versatile").generate(REQUEST)
    assert "reasoning_effort" not in seen[0]


@pytest.mark.parametrize(
    ("response", "daily", "retry_after"),
    [
        (
            httpx.Response(
                429, headers={"retry-after": "7"}, text="Rate limit reached on requests per minute"
            ),
            False,
            7.0,
        ),
        (httpx.Response(429, text="Rate limit reached on requests per day (RPD)"), True, None),
        (httpx.Response(429, headers={"retry-after": "soon"}, text="limit"), False, None),
    ],
)
def test_groq_rate_limits(
    response: httpx.Response, *, daily: bool, retry_after: float | None
) -> None:
    provider = groq(lambda _: response)
    with pytest.raises(RateLimitedError) as error:
        provider.generate(REQUEST)
    assert (error.value.daily, error.value.retry_after_s) == (daily, retry_after)


def raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def raise_connect(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (raise_timeout, ProviderTimeoutError),
        (raise_connect, ProviderUnavailableError),
        (lambda _: httpx.Response(503), ProviderUnavailableError),
        (lambda _: httpx.Response(401), ProviderUnavailableError),
        (lambda _: httpx.Response(200, text="not json"), ProviderUnavailableError),
        (lambda _: httpx.Response(200, json={"choices": []}), ProviderUnavailableError),
        (lambda _: answer(""), ProviderUnavailableError),
    ],
)
def test_groq_failures_are_typed(
    handler: Callable[[httpx.Request], httpx.Response], expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        groq(handler).generate(REQUEST)


# Factory


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **values)


def test_providers_follow_the_chain_order() -> None:
    providers = build_providers(
        settings(
            llm_chain="gemini:a, groq:b , gemini:c",
            gemini_api_key="g",
            groq_api_key="q",
        )
    )
    assert [p.name for p in providers] == ["gemini:a", "groq:b", "gemini:c"]


def test_providers_without_keys_are_skipped() -> None:
    assert [
        p.name for p in build_providers(settings(llm_chain="gemini:a,groq:b", groq_api_key="q"))
    ] == ["groq:b"]
    assert build_providers(settings(llm_chain="gemini:a,groq:b")) == []


@pytest.mark.parametrize(
    ("chain", "message"), [("gemini", "provider:model"), ("openai:gpt", "unknown provider")]
)
def test_malformed_chain_fails_fast(chain: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_providers(settings(llm_chain=chain, gemini_api_key="g"))
