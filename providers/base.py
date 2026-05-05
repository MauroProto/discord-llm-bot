"""Abstract base class for LLM providers.

All providers (Anthropic, OpenAI, Gemini, OpenRouter, ...) implement this
interface so the rest of the bot can stay agnostic. The contract is small on
purpose: only the two entry-points `generate_response` (chat) and
`generate_voice_response` (TTS-bound, shorter) plus a capability probe.

Capabilities let the caller branch on features that are not universally
supported (e.g. native web search server-side, or 1M-token context windows).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto


class Capability(Enum):
    """Optional capabilities a provider may advertise via `supports()`."""

    REASONING = auto()        # extended thinking / chain-of-thought tokens
    WEB_SEARCH = auto()       # native server-side web search tool
    WEB_FETCH = auto()        # native server-side URL fetch tool
    IMAGE_INPUT = auto()      # multimodal image input
    PDF_INPUT = auto()        # PDF file input
    PROMPT_CACHE = auto()     # prompt prefix caching
    CONTEXT_1M = auto()       # 1M-token context window
    STREAMING = auto()        # streaming response API


class LLMProvider(ABC):
    """Common interface every LLM provider exposes to the bot."""

    name: str

    @abstractmethod
    async def generate_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ) -> str:
        """Generate a chat-style response.

        Args:
            messages: Conversation in Anthropic-style format
                `[{"role": "user"|"assistant", "content": str | list[dict]}]`.
                Multimodal content blocks (image, document) follow the
                Anthropic shape; each provider translates internally.
            use_search: Caller hint that web search may help. Native-search
                providers honor it; others may noop.
            search_query: Optional precomputed search query.
            memory_text: Long-term memory prefix to inject into the system
                prompt. Providers that support prompt caching should mark it
                as cacheable.

        Returns:
            The plain-text response. Empty string on hard failure (after
            retries / friendly error message).
        """
        ...

    @abstractmethod
    async def generate_voice_response(
        self,
        messages: list[dict],
        memory_text: str = "",
        max_chars: int | None = None,
    ) -> str:
        """Generate a response tuned for TTS playback.

        Should produce shorter, punctuation-heavy, markdown-free text. The
        provider is responsible for swapping to a faster/cheaper model and
        disabling reasoning if those make sense for its lineup.
        """
        ...

    @abstractmethod
    async def analyze_conversation(self, history_text: str, task: str) -> str:
        """Run a one-shot analytical task over a conversation transcript.

        Used by commands like `!summary`. No memory injection.
        """
        ...

    def supports(self, capability: Capability) -> bool:
        """Whether this provider supports a given capability.

        Default is False — providers should override and return True for the
        ones they actually expose.
        """
        return False


__all__ = ["LLMProvider", "Capability"]
