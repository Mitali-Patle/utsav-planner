"""Google Gemini adapter (google-genai SDK).

The SDK is imported on first use, not at startup, so pages that never call the AI keep a
fast cold start.
"""

import re
from typing import Any

import httpx
from pydantic import SecretStr

from app.ai.client import (
    LLMRequest,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)

HTTP_TOO_MANY_REQUESTS = 429
# Gemini answers 400 "Manually set deadline 8s is too short. Minimum allowed deadline is 10s."
MIN_DEADLINE_MS = 10_000
TIMEOUT_CODES = frozenset({408, 504})
_SECONDS = re.compile(r"^(\d+(?:\.\d+)?)s$")


class GeminiProvider:
    """Calls one Gemini model with structured JSON output."""

    def __init__(self, model: str, api_key: SecretStr, *, client: Any = None) -> None:  # noqa: ANN401 - SDK client
        """Create the adapter.

        Args:
            model: Model name, for example `gemini-3.5-flash-lite`.
            api_key: Server-side Gemini API key.
            client: Pre-built SDK client (tests); created lazily when omitted.
        """
        self._model = model
        self._api_key = api_key
        self._client = client

    @property
    def name(self) -> str:
        """Identifier used in provenance and cooldowns."""
        return f"gemini:{self._model}"

    def generate(self, request: LLMRequest) -> str:
        """Return the model's JSON answer.

        Raises:
            RateLimitedError: On HTTP 429, with daily vs per-minute and the retry delay.
            ProviderTimeoutError: On a timeout.
            ProviderUnavailableError: On server errors, rejected requests or empty answers.
        """
        from google.genai import errors, types  # noqa: PLC0415 - lazy import for cold starts

        config = types.GenerateContentConfig(
            system_instruction=request.system,
            response_mime_type="application/json",
            response_schema=request.schema,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            http_options=types.HttpOptions(
                timeout=max(MIN_DEADLINE_MS, round(request.timeout_s * 1000))
            ),
        )
        contents = [
            types.Content(role=role, parts=[types.Part(text=text)])
            for role, text in request.messages
        ]
        try:
            response = self._sdk_client().models.generate_content(
                model=self._model, contents=contents, config=config
            )
        except errors.APIError as error:
            raise _map_api_error(error.code, error.status, error.details) from error
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError(f"{self.name} timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError(f"{self.name} network error") from error

        text: str | None = response.text
        if not text:
            raise ProviderUnavailableError(f"{self.name} returned an empty answer")
        return text

    def _sdk_client(self) -> Any:  # noqa: ANN401 - SDK client
        if self._client is None:
            from google import genai  # noqa: PLC0415 - lazy import for cold starts

            self._client = genai.Client(api_key=self._api_key.get_secret_value())
        return self._client


def _map_api_error(code: int, status: str | None, details: Any) -> ProviderError:  # noqa: ANN401 - raw API JSON
    """Translate a Gemini API error into the chain's error types.

    Messages carry only the HTTP code and status (for example `503 UNAVAILABLE`), never keys
    or request content.
    """
    label = f"HTTP {code} {status or ''}".strip()
    if code == HTTP_TOO_MANY_REQUESTS:
        daily, retry_after = _quota_details(details)
        return RateLimitedError(
            f"Gemini quota exhausted ({label})", daily=daily, retry_after_s=retry_after
        )
    if code in TIMEOUT_CODES:
        return ProviderTimeoutError(f"Gemini timed out ({label})")
    return ProviderUnavailableError(f"Gemini returned {label}")


def _quota_details(details: Any) -> tuple[bool, float | None]:  # noqa: ANN401 - raw API JSON
    """Read whether a 429 is a daily quota and how long to wait, from the error details.

    Gemini reports `QuotaFailure` violations (quota ids containing `PerDay` for daily limits)
    and a `RetryInfo` with a delay such as `"34s"`.
    """
    error = details.get("error", {}) if isinstance(details, dict) else {}
    items = [item for item in error.get("details", []) if isinstance(item, dict)]
    daily = any(
        "PerDay" in str(violation.get("quotaId", ""))
        for item in items
        for violation in item.get("violations", [])
        if isinstance(violation, dict)
    )
    retry_after = None
    for item in items:
        match = _SECONDS.match(str(item.get("retryDelay", "")))
        if match:
            retry_after = float(match.group(1))
    return daily, retry_after
