"""Groq adapter (OpenAI-compatible chat completions over httpx).

Uses non-strict JSON-schema mode: Groq's strict mode needs `additionalProperties: false` on
every object, and our own Pydantic validation is the strict check anyway.
"""

import httpx
from pydantic import SecretStr

from app.ai.client import (
    LLMRequest,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HTTP_OK = 200
HTTP_TOO_MANY_REQUESTS = 429
_ROLES = {"user": "user", "model": "assistant"}


class GroqProvider:
    """Calls one Groq-hosted model with JSON output."""

    def __init__(
        self, model: str, api_key: SecretStr, *, http_client: httpx.Client | None = None
    ) -> None:
        """Create the adapter.

        Args:
            model: Model name, for example `openai/gpt-oss-120b`.
            api_key: Server-side Groq API key.
            http_client: Pre-built client (tests use a mock transport).
        """
        self._model = model
        self._api_key = api_key
        self._http = http_client or httpx.Client()

    @property
    def name(self) -> str:
        """Identifier used in provenance and cooldowns."""
        return f"groq:{self._model}"

    def generate(self, request: LLMRequest) -> str:
        """Return the model's JSON answer.

        Raises:
            RateLimitedError: On HTTP 429, with daily vs per-minute and the retry delay.
            ProviderTimeoutError: On a timeout.
            ProviderUnavailableError: On other HTTP errors or a malformed body.
        """
        try:
            response = self._http.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                json=self._body(request),
                timeout=request.timeout_s,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError(f"{self.name} timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError(f"{self.name} network error") from error

        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            raise RateLimitedError(
                "Groq rate limit reached",
                daily="per day" in response.text.lower() or "rpd" in response.text.lower(),
                retry_after_s=_retry_after(response),
            )
        if response.status_code != HTTP_OK:
            raise ProviderUnavailableError(f"{self.name} returned HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ProviderUnavailableError(f"{self.name} returned a malformed body") from error
        if not isinstance(content, str) or not content:
            raise ProviderUnavailableError(f"{self.name} returned an empty answer")
        return content

    def _body(self, request: LLMRequest) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                *({"role": _ROLES[role], "content": text} for role, text in request.messages),
            ],
            "temperature": request.temperature,
            "max_completion_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema.__name__,
                    "schema": request.schema.model_json_schema(),
                },
            },
        }
        if "gpt-oss" in self._model:
            body["reasoning_effort"] = "low"
        return body


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None
