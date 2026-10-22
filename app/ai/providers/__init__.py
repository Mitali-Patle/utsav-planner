"""LLM provider adapters and the factory that builds the chain order from settings."""

import logging

from app.ai.client import Provider
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.groq import GroqProvider
from app.config import Settings

logger = logging.getLogger(__name__)


def build_providers(settings: Settings) -> list[Provider]:
    """Create providers in the order given by `LLM_CHAIN`.

    Entries look like `gemini:gemini-3.5-flash-lite` or `groq:openai/gpt-oss-120b`.
    Providers without an API key are skipped with a warning, so a missing backup key never
    stops the app; with no providers at all, every call uses the labelled fallback.

    Args:
        settings: Application settings.

    Returns:
        Providers in order of preference.

    Raises:
        ValueError: If an entry is malformed or names an unknown provider.
    """
    providers: list[Provider] = []
    for entry in (part.strip() for part in settings.llm_chain.split(",")):
        kind, _, model = entry.partition(":")
        if not model:
            raise ValueError(f"LLM_CHAIN entry '{entry}' must look like 'provider:model'")
        if kind == "gemini":
            if settings.gemini_api_key is None:
                logger.warning("LLM_CHAIN: skipping %s, GEMINI_API_KEY is not set", entry)
                continue
            providers.append(GeminiProvider(model, settings.gemini_api_key))
        elif kind == "groq":
            if settings.groq_api_key is None:
                logger.warning("LLM_CHAIN: skipping %s, GROQ_API_KEY is not set", entry)
                continue
            providers.append(GroqProvider(model, settings.groq_api_key))
        else:
            raise ValueError(f"LLM_CHAIN entry '{entry}' names an unknown provider '{kind}'")
    return providers
