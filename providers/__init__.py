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


def get_provider(name: str | None = None) -> "LLMProvider":
    """Return the provider instance for `name` (or `settings.LLM_PROVIDER`)."""
    name = (name or settings.LLM_PROVIDER).lower().strip()

    cached = _provider_cache.get(name)
    if cached is not None:
        return cached

    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        provider = AnthropicProvider()
    elif name == "openai":
        from .openai_provider import OpenAIProvider
        provider = OpenAIProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider {name!r}. "
            f"Supported: anthropic, openai. (Gemini and OpenRouter coming next.)"
        )

    _provider_cache[name] = provider
    return provider


__all__ = ["get_provider"]
