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
from typing import TYPE_CHECKING

from config import settings

if TYPE_CHECKING:
    from personalities import Personality


def warn_if_mcp_configured(provider_name: str) -> None:
    """Log a one-time warning if MCP servers are configured on a provider
    that doesn't support them natively (everyone except Anthropic for now).
    """
    if not settings.MCP_SERVERS_JSON:
        return
    if getattr(warn_if_mcp_configured, "_warned", set()).__contains__(provider_name):  # type: ignore[attr-defined]
        return
    warn_if_mcp_configured.__dict__.setdefault("_warned", set()).add(provider_name)
    print(
        f"[mcp] {provider_name!r} provider doesn't support remote MCP servers natively; "
        f"MCP_SERVERS_JSON is ignored. Switch to LLM_PROVIDER=anthropic to use MCP."
    )


def resolve_personality(bot_name: str = "the bot") -> "Personality":
    """Pick the active personality based on env config.

    Resolution order (from `personalities.loader.load_personality`):
      1. `CUSTOM_SYSTEM_PROMPT` (literal text override)
      2. `SYSTEM_PROMPT` (legacy alias of #1, deprecated)
      3. `CUSTOM_SYSTEM_PROMPT_FILE` (file override)
      4. `BOT_PERSONALITY` -> `personalities/{id}.md` (default 'friendly')

    `bot_name` is substituted for `{{BOT_NAME}}` in the prompt.
    """
    from personalities import load_personality

    # Honour the legacy SYSTEM_PROMPT env var as an alias of
    # CUSTOM_SYSTEM_PROMPT. Done here (rather than in pydantic validators)
    # so we can warn about it at the right moment.
    if settings.SYSTEM_PROMPT and not settings.CUSTOM_SYSTEM_PROMPT:
        # Stash into CUSTOM_SYSTEM_PROMPT so the loader sees it.
        settings.CUSTOM_SYSTEM_PROMPT = settings.SYSTEM_PROMPT  # type: ignore[misc]

    return load_personality(bot_name=bot_name)


class Capability(Enum):
    """Optional capabilities a provider may advertise via `supports()`."""


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

    # Subclasses set this in __init__ via resolve_personality(...).
    _personality: "Personality"

    # ------- Shared voice suffix construction -------

    @staticmethod
    def _build_shared_voice_rules() -> str:
        """Voice rules every personality inherits — audio tag handling per
        TTS model and the `[CHAT:]/[SOLO_CHAT:]` inline marker contract.

        These are system contract, not style: regardless of personality, the
        bot must follow them so the voice pipeline works correctly.
        """
        is_v3 = settings.ELEVENLABS_TTS_MODEL == "eleven_v3"

        rules = "\n\n## Voice mode shared rules (system contract)\n\n"

        if is_v3:
            rules += (
                "### Audio tags (ElevenLabs v3)\n"
                "You may include audio tags in square brackets — v3 renders them as real "
                "emotion. Use them when they add something, max 0-2 per reply.\n"
                "Useful tags: [laughs], [chuckles], [whispers], [sighs], [excited], [sad], "
                "[thoughtful], [sarcastic]. Don't sprinkle them.\n\n"
            )
        else:
            rules += (
                "### No audio tags\n"
                "**Never write bracketed audio tags** like [laughs], [whispers], [sighs], "
                "[excited], etc. The current TTS model pronounces them literally (it would "
                "say the word 'laughs' out loud) which breaks immersion. Express emotion "
                "with words instead (e.g. 'haha', 'phew').\n\n"
            )

        rules += (
            "### Sending to the text chat from voice\n"
            "While speaking on the call, you can also write to the linked text channel. "
            "This makes you feel more present — like someone on a call who drops a useful "
            "message in chat alongside speaking.\n\n"
            "Use these markers (literal syntax):\n"
            "- **Speak normally**: just reply. Everything is spoken via TTS.\n"
            "- **Speak AND send to chat**: end with `[CHAT: content for the chat]`. "
            "Anything BEFORE the marker is spoken aloud; the marker's contents go to the "
            "text channel. The marker itself is not read aloud.\n"
            "- **Only send to chat (don't speak)**: reply with `[SOLO_CHAT: content]`. "
            "Useful when the user asks you to 'send it in chat' or when the answer is a "
            "URL, code block, or long list that doesn't belong in speech.\n\n"
            "Examples:\n"
            "  User (voice): 'send me the API docs link'\n"
            "  You: 'Sending it in chat. [CHAT: https://example.com/docs]'\n\n"
            "  User (voice): 'write out the 5 deploy steps'\n"
            "  You: '[SOLO_CHAT: 1. Build  2. Push  3. Connect Railway  4. Set env vars  5. Deploy]'\n\n"
            "  User (voice): 'how was yesterday's bug'\n"
            "  You (no chat): 'Fixed it late, runs fine now.'\n\n"
            "Use it when it adds real value (links, code, lists, long info). For normal "
            "chat back-and-forth, just speak — no markers needed.\n"
        )

        return rules

    def _build_voice_suffix(self) -> str:
        """Append the personality's voice section + shared system contract."""
        suffix = "\n\n---\n\n# Voice mode\n\n"

        personality_voice = getattr(self, "_personality", None)
        if personality_voice and personality_voice.voice_section:
            suffix += personality_voice.voice_section + "\n"
        else:
            suffix += (
                "This response will be played through TTS. Keep it to 1-2 short, natural "
                "sentences. No markdown, no URLs, no lists, no emojis — TTS reads them "
                "literally. For long answers, route them to chat (see shared rules below).\n"
            )

        suffix += self._build_shared_voice_rules()
        return suffix

    async def stream_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ):
        """Stream a chat-style response, yielding text chunks as they arrive.

        Default implementation: call the non-streaming `generate_response`
        and yield the whole result at once. Providers override this with a
        true streaming version that yields incremental deltas — useful for
        editing a Discord message progressively as the LLM generates.

        Yields:
            `str` chunks. Concatenating all yielded chunks reproduces the
            full response.
        """
        full = await self.generate_response(
            messages=messages,
            use_search=use_search,
            search_query=search_query,
            memory_text=memory_text,
        )
        if full:
            yield full

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
