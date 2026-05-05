"""Anthropic Claude provider.

Wraps the AsyncAnthropic SDK with the bot's prompt caching, adaptive thinking,
native web_search / web_fetch tools, and voice mode adjustments. This module
holds the same behavior the bot has run with since the project's original
single-provider days; the abstraction merely lets us swap in OpenAI, Gemini,
and OpenRouter alongside it.
"""

from __future__ import annotations

import re

import anthropic
from anthropic import AsyncAnthropic

from config import settings

from .base import Capability, LLMProvider, resolve_personality


_URL_RE = re.compile(r"https?://\S+")


def _clean_err(e: Exception) -> str:
    """Sanitize error text: strip URLs (Discord auto-embeds) and limit length."""
    text = str(e).split("\n")[0]
    text = _URL_RE.sub("", text)
    text = text.replace("`", "").strip()
    return text[:180] if text else type(e).__name__


class AnthropicProvider(LLMProvider):
    """Async client for Anthropic Claude with native search support."""

    name = "anthropic"

    CONTEXT_1M_BETA = "context-1m-2025-08-07"
    WEB_FETCH_BETA = "web-fetch-2025-09-10"
    MCP_CLIENT_BETA = "mcp-client-2025-11-20"
    WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
    WEB_FETCH_TOOL_TYPE = "web_fetch_20250910"

    def __init__(self) -> None:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is empty. Set it in .env (or switch to a "
                "different LLM_PROVIDER). Run `discord-llm-bot setup` to reconfigure."
            )
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.ANTHROPIC_MODEL
        self.max_tokens = settings.MAX_TOKENS
        # Personality is resolved from env config (CUSTOM_SYSTEM_PROMPT, then
        # CUSTOM_SYSTEM_PROMPT_FILE, then personalities/<BOT_PERSONALITY>.md).
        self._personality = resolve_personality(
            bot_name=settings.BOT_DISPLAY_NAME or "the bot",
        )
        self.system_prompt = self._personality.chat_prompt

        betas: list[str] = []
        if settings.ENABLE_1M_CONTEXT:
            betas.append(self.CONTEXT_1M_BETA)
        if settings.ENABLE_WEB_FETCH:
            betas.append(self.WEB_FETCH_BETA)
        self.extra_headers: dict[str, str] = (
            {"anthropic-beta": ",".join(betas)} if betas else {}
        )

        # Opus 4.7 uses adaptive thinking + output_config.effort.
        # The legacy {"type": "enabled", "budget_tokens": N} format returns
        # 400 on Opus 4.7 — keep adaptive.
        self.thinking_param: dict | None = None
        self.output_config: dict | None = None
        if settings.EXTENDED_THINKING:
            self.thinking_param = {"type": "adaptive"}
            self.output_config = {"effort": settings.THINKING_EFFORT}

        tools: list[dict] = []
        if settings.ENABLE_WEB_SEARCH:
            tools.append({
                "type": self.WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": settings.WEB_SEARCH_MAX_USES,
            })
        if settings.ENABLE_WEB_FETCH:
            tools.append({
                "type": self.WEB_FETCH_TOOL_TYPE,
                "name": "web_fetch",
                "max_uses": settings.WEB_FETCH_MAX_USES,
            })

        # MCP servers (Anthropic's native server-side connector). When
        # configured, we add the matching mcp_toolset entries to `tools`
        # and the server defs to `mcp_servers` (set on each request) plus
        # the MCP beta header. Other providers ignore MCP for now.
        from mcp_config import load_servers, to_anthropic_payload
        self._mcp_servers: list[dict] = []
        try:
            mcp_list = load_servers()
            if mcp_list:
                self._mcp_servers, mcp_toolsets = to_anthropic_payload(mcp_list)
                tools.extend(mcp_toolsets)
                if self.MCP_CLIENT_BETA not in betas:
                    betas.append(self.MCP_CLIENT_BETA)
                    self.extra_headers = {"anthropic-beta": ",".join(betas)}
                print(f"[mcp] anthropic provider connected to {len(mcp_list)} MCP server(s): "
                      f"{', '.join(s['name'] for s in self._mcp_servers)}")
        except Exception as e:
            print(f"[mcp] failed to configure MCP servers (continuing without): {e}")

        self.tools: list[dict] | None = tools or None

    # ------- Capabilities -------

    def supports(self, capability: Capability) -> bool:
        return capability in {
            Capability.REASONING,
            Capability.WEB_SEARCH,
            Capability.WEB_FETCH,
            Capability.IMAGE_INPUT,
            Capability.PDF_INPUT,
            Capability.PROMPT_CACHE,
            Capability.CONTEXT_1M,
            Capability.STREAMING,
        }

    # ------- Prompt construction -------

    def _build_system_prompt(self, memory_text: str = "") -> str | list[dict]:
        """Compose the system prompt. When `memory_text` is non-empty we
        return it as a list of two blocks so Anthropic's prompt caching can
        keep the long memory block warm across calls (saves 1-3s of latency
        per request)."""
        if not memory_text:
            return self.system_prompt

        memory_block = (
            "# Memoria interna del grupo (conversaciones guardadas automáticamente)\n\n"
            "Lo siguiente es un registro de lo que ya hablaron en este grupo en días anteriores. "
            "Usalo como contexto de fondo: si te preguntan algo que se discutió antes, ya lo sabés. "
            "No menciones que tenés un sistema de memoria ni te refieras a este texto explícitamente; "
            "simplemente actuá como una integrante del grupo que se acuerda de lo que pasó.\n\n"
            + memory_text
        )

        # Block 1 = personality (small, stable). Block 2 = memory (large,
        # cacheable). Subsequent calls with the same memory reuse the cache.
        return [
            {"type": "text", "text": self.system_prompt},
            {
                "type": "text",
                "text": memory_block,
                "cache_control": {"type": "ephemeral"},
            },
        ]

    # ------- Streaming chat generation -------

    async def stream_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ):
        """Stream Claude responses chunk-by-chunk.

        Anthropic's `messages.stream` yields events; we forward only the
        text deltas so the caller can edit a Discord message in place.
        Thinking tokens and tool blocks are filtered out.
        """
        try:
            create_kwargs: dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": self._build_system_prompt(memory_text),
                "messages": messages,
            }
            if self.extra_headers:
                create_kwargs["extra_headers"] = self.extra_headers
            if self.thinking_param:
                create_kwargs["thinking"] = self.thinking_param
            if self.output_config:
                create_kwargs["output_config"] = self.output_config
            if self.tools:
                create_kwargs["tools"] = self.tools
            if self._mcp_servers:
                create_kwargs["mcp_servers"] = self._mcp_servers

            async with self.client.messages.stream(**create_kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text

        except anthropic.APIError as e:
            yield f"Che, la API de Claude se quejó. Reintentame. ({_clean_err(e)})"
        except Exception as e:
            yield f"Ups, algo se rompió. Reintentame en un toque. ({_clean_err(e)})"

    # ------- Chat generation -------

    async def generate_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ) -> str:
        """Generate a response with native web_search/web_fetch when enabled."""
        try:
            create_kwargs: dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": self._build_system_prompt(memory_text),
                "messages": messages,
            }
            if self.extra_headers:
                create_kwargs["extra_headers"] = self.extra_headers
            if self.thinking_param:
                create_kwargs["thinking"] = self.thinking_param
            if self.output_config:
                create_kwargs["output_config"] = self.output_config
            if self.tools:
                create_kwargs["tools"] = self.tools
            if self._mcp_servers:
                create_kwargs["mcp_servers"] = self._mcp_servers

            # Always stream — Anthropic requires it for long-running ops
            # (>10 min) such as extended thinking with high budgets or 1M
            # context.
            async with self.client.messages.stream(**create_kwargs) as stream:
                final_message = await stream.get_final_message()

            # Concatenate all text blocks (skip thinking & tool blocks).
            parts = [
                block.text
                for block in final_message.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", None)
            ]
            return "\n".join(parts).strip()

        except anthropic.APIError as e:
            return f"Che, la API de Claude se quejó. Reintentame. ({_clean_err(e)})"
        except Exception as e:
            return f"Ups, algo se rompió. Reintentame en un toque. ({_clean_err(e)})"

    # ------- Voice generation -------

    # `_build_voice_suffix()` and `_build_shared_voice_rules()` are inherited
    # from `LLMProvider` — they use `self._personality` set in __init__.

    async def generate_voice_response(
        self,
        messages: list[dict],
        memory_text: str = "",
        max_chars: int | None = None,
    ) -> str:
        """Like `generate_response` but tuned for TTS playback.

        Overrides thinking/effort and the model based on `VOICE_*` settings,
        leaving the chat-mode config intact (chat keeps MAX reasoning).
        """
        max_chars = max_chars or settings.VOICE_MAX_RESPONSE_CHARS

        # Snapshot live config so we can swap and restore.
        original_system = self.system_prompt
        original_thinking = self.thinking_param
        original_output = self.output_config
        original_model = self.model

        try:
            # 1) System prompt: append voice-mode instructions (TTS-aware).
            self.system_prompt = original_system + self._build_voice_suffix()

            # 2) Reasoning: lower or disable for minimum latency.
            if not settings.VOICE_EXTENDED_THINKING:
                self.thinking_param = None
                self.output_config = None
            else:
                self.thinking_param = {"type": "adaptive"}
                self.output_config = {"effort": settings.VOICE_THINKING_EFFORT}

            # 3) Voice model: usually a faster/cheaper one (Haiku).
            voice_model = (settings.VOICE_CLAUDE_MODEL or "").strip()
            if voice_model:
                self.model = voice_model

            response = await self.generate_response(
                messages=messages,
                memory_text=memory_text,
            )
        finally:
            self.system_prompt = original_system
            self.thinking_param = original_thinking
            self.output_config = original_output
            self.model = original_model

        # Defensive truncation in case the model overshoots.
        if len(response) > max_chars:
            cut = response[:max_chars].rsplit(".", 1)[0]
            response = (cut + ".") if cut else response[:max_chars]
        return response

    # ------- Conversation analysis -------

    async def analyze_conversation(self, history_text: str, task: str) -> str:
        """Analyze a conversation with a specific task (e.g. summarize)."""
        messages = [{
            "role": "user",
            "content": f"{task}\n\nConversacion:\n{history_text}",
        }]
        return await self.generate_response(messages)


__all__ = ["AnthropicProvider"]
