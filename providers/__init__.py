"""LLM provider registry.

Exposes a single entry-point `get_provider(name)` that returns the configured
provider instance. Providers implement `LLMProvider` (see `base.py`) and offer
a uniform interface for chat and voice response generation regardless of the
underlying SDK (Anthropic, OpenAI, Gemini, OpenRouter, ...).

The provider used at runtime is selected by the `LLM_PROVIDER` env var
(default `anthropic`) — see `config.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from config import settings

if TYPE_CHECKING:
    from .base import LLMProvider


# Cache of resolved provider instances keyed by normalized name. Providers
# hold underlying SDK clients with connection pools, so we want to share the
# same instance for the lifetime of the process.
_provider_cache: dict[str, "LLMProvider"] = {}


# Aliases collapse to a single canonical name so the cache returns one
# instance per actual provider class.
_ALIASES = {
    "google": "gemini",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "gpt": "openai",
    "gemini": "gemini",
    "openrouter": "openrouter",
    "or": "openrouter",
    "ollama": "ollama",
    "local": "ollama",
}


def get_provider(name: str | None = None) -> "LLMProvider":
    """Return the provider instance for `name` (or `settings.LLM_PROVIDER`)."""
    raw = (name or settings.LLM_PROVIDER).lower().strip()
    canonical = _ALIASES.get(raw, raw)

    cached = _provider_cache.get(canonical)
    if cached is not None:
        return cached

    if canonical == "anthropic":
        from .anthropic_provider import AnthropicProvider
        provider = AnthropicProvider()
    elif canonical == "openai":
        from .openai_provider import OpenAIProvider
        provider = OpenAIProvider()
    elif canonical == "gemini":
        from .gemini_provider import GeminiProvider
        provider = GeminiProvider()
    elif canonical == "openrouter":
        from .openrouter_provider import OpenRouterProvider
        provider = OpenRouterProvider()
    elif canonical == "ollama":
        from .ollama_provider import OllamaProvider
        provider = OllamaProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider {raw!r}. "
            f"Supported: anthropic, openai, gemini, openrouter, ollama."
        )

    _provider_cache[canonical] = provider
    return provider


__all__ = ["get_provider"]
