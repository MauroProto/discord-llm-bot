"""OpenAI provider using the Responses API.

The Responses API is OpenAI's newer surface (the `client.responses.*` family)
that exposes reasoning effort for the o-series and GPT-5 models, native
`web_search` tool, and input_file support for PDFs without needing the
Assistants infrastructure.

Multimodal inputs (images, PDFs) are accepted in the same Anthropic-style
content blocks the rest of the bot already builds — this module translates
them on the fly. Plain string messages keep working unchanged.

Prompt caching is automatic on OpenAI: stable prefixes ≥1024 tokens hit a
warm cache without us having to mark anything. We just keep the system
prompt + memory in a stable position and the platform does the rest.
"""

from __future__ import annotations

import re
from typing import Any

from config import settings

from .base import Capability, LLMProvider, resolve_personality


_URL_RE = re.compile(r"https?://\S+")


def _clean_err(e: Exception) -> str:
    text = str(e).split("\n")[0]
    text = _URL_RE.sub("", text)
    text = text.replace("`", "").strip()
    return text[:180] if text else type(e).__name__


def _is_reasoning_model(model: str) -> bool:
    """Whether this OpenAI model supports the `reasoning.effort` parameter."""
    m = model.lower()
    return (
        m.startswith("o1") or m.startswith("o3") or m.startswith("o4")
        or m.startswith("gpt-5") or m.startswith("gpt5")
    )


def _map_effort(effort: str) -> str:
    """Map our internal effort levels to OpenAI's accepted ones (low/medium/high).

    OpenAI accepts `low`, `medium`, `high`. Anthropic's adaptive thinking
    accepts `low`, `medium`, `high`, `xhigh`, `max`. Map them sensibly.
    """
    e = (effort or "").lower().strip()
    if e in {"low", "medium", "high"}:
        return e
    if e in {"xhigh", "max"}:
        return "high"
    return "medium"


class OpenAIProvider(LLMProvider):
    """OpenAI Responses API client."""

    name = "openai"

    def __init__(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError(
                "OpenAI provider requires the `openai` package. "
                "Install it with: pip install openai"
            ) from e

        if not settings.OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai. "
                "Get one at https://platform.openai.com/api-keys"
            )

        kwargs: dict[str, Any] = {"api_key": settings.OPENAI_API_KEY}
        if settings.OPENAI_BASE_URL:
            kwargs["base_url"] = settings.OPENAI_BASE_URL  # used for OpenRouter, etc.

        self.client = AsyncOpenAI(**kwargs)

        # Default to GPT-5 family if user didn't pick. Users with reasoning
        # needs can pick o3 / o4-mini explicitly via env var.
        self.model = settings.OPENAI_MODEL or "gpt-5.4"
        self.max_tokens = settings.MAX_TOKENS
        self._personality = resolve_personality(
            bot_name=settings.BOT_DISPLAY_NAME or "the bot",
        )
        self.system_prompt = self._personality.chat_prompt

        # Reasoning is configured per call. We just snapshot the global
        # settings here; voice mode overrides them temporarily.
        self.reasoning_enabled = settings.EXTENDED_THINKING
        self.reasoning_effort = settings.THINKING_EFFORT

        # Native web search: include the tool when enabled.
        self.tools: list[dict] | None = (
            [{"type": "web_search"}] if settings.ENABLE_WEB_SEARCH else None
        )

    # ------- Capabilities -------

    def supports(self, capability: Capability) -> bool:
        # Reasoning depends on the chosen model; this is a static
        # advertisement so we keep it True for the provider class. Callers
        # should still gate on model where it matters.
        return capability in {
            Capability.REASONING,
            Capability.WEB_SEARCH,
            Capability.IMAGE_INPUT,
            Capability.PDF_INPUT,
            Capability.PROMPT_CACHE,  # automatic on OpenAI for ≥1024 token prefixes
            Capability.STREAMING,
        }

    # ------- Message translation -------

    @staticmethod
    def _translate_content(content: Any) -> Any:
        """Convert Anthropic-style content into OpenAI Responses format."""
        if isinstance(content, str):
            return content

        if not isinstance(content, list):
            return str(content)

        translated: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                translated.append({"type": "input_text", "text": str(block)})
                continue

            btype = block.get("type")

            if btype == "text":
                translated.append({"type": "input_text", "text": block.get("text", "")})

            elif btype == "image":
                source = block.get("source", {}) or {}
                if source.get("type") == "base64":
                    media = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    translated.append({
                        "type": "input_image",
                        "image_url": f"data:{media};base64,{data}",
                    })
                elif source.get("type") == "url":
                    translated.append({
                        "type": "input_image",
                        "image_url": source.get("url", ""),
                    })

            elif btype == "document":
                # Anthropic PDF block. OpenAI Responses accepts file_data URI.
                source = block.get("source", {}) or {}
                if source.get("type") == "base64":
                    media = source.get("media_type", "application/pdf")
                    data = source.get("data", "")
                    translated.append({
                        "type": "input_file",
                        "filename": "document.pdf",
                        "file_data": f"data:{media};base64,{data}",
                    })

            else:
                # Unknown block type — try to coerce to text.
                if "text" in block:
                    translated.append({"type": "input_text", "text": str(block["text"])})

        return translated or content

    @classmethod
    def _translate_messages(cls, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            role = m.get("role", "user")
            # OpenAI Responses uses the same role names we already use.
            out.append({"role": role, "content": cls._translate_content(m.get("content", ""))})
        return out

    # ------- Prompt construction -------

    def _build_instructions(self, memory_text: str = "") -> str:
        """OpenAI uses a single `instructions` string (no cache_control needed
        — automatic prefix caching applies). We concatenate personality +
        memory so the prefix stays stable across calls."""
        if not memory_text:
            return self.system_prompt
        return (
            self.system_prompt
            + "\n\n---\n\n"
            "# Long-term memory (auto-saved conversations)\n\n"
            "The following is a record of what was discussed in previous sessions. "
            "Use it as background context. Do not mention this memory system or "
            "reference this text explicitly — just behave like a member of the "
            "group who remembers what happened.\n\n"
            + memory_text
        )

    # ------- Chat generation -------

    async def generate_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ) -> str:
        try:
            create_kwargs: dict[str, Any] = {
                "model": self.model,
                "instructions": self._build_instructions(memory_text),
                "input": self._translate_messages(messages),
                "max_output_tokens": self.max_tokens,
                "stream": True,
            }

            if self.tools:
                create_kwargs["tools"] = self.tools

            if self.reasoning_enabled and _is_reasoning_model(self.model):
                create_kwargs["reasoning"] = {"effort": _map_effort(self.reasoning_effort)}

            return await self._stream_collect(create_kwargs)

        except Exception as e:
            return f"OpenAI hiccupped, retry me. ({_clean_err(e)})"

    async def _stream_collect(self, create_kwargs: dict[str, Any]) -> str:
        """Open a streaming Responses call and collect the final text."""
        # The Responses streaming surface yields events; we only need the
        # final aggregated text. `await client.responses.create(stream=True)`
        # returns an async iterator of events.
        text_parts: list[str] = []
        async with self.client.responses.stream(**create_kwargs) as stream:
            async for event in stream:
                # Different event types: response.output_text.delta carries
                # incremental tokens; we accumulate and return at the end.
                etype = getattr(event, "type", "")
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    text_parts.append(delta)
                elif etype == "response.error":
                    err = getattr(event, "error", None) or "unknown error"
                    raise RuntimeError(str(err))
            # Optional: fetch the final response object for usage info.
            await stream.get_final_response()

        return "".join(text_parts).strip()

    # ------- Voice generation -------

    async def generate_voice_response(
        self,
        messages: list[dict],
        memory_text: str = "",
        max_chars: int | None = None,
    ) -> str:
        max_chars = max_chars or settings.VOICE_MAX_RESPONSE_CHARS

        original_system = self.system_prompt
        original_reasoning_enabled = self.reasoning_enabled
        original_effort = self.reasoning_effort
        original_model = self.model

        try:
            # Append the personality's voice section + shared rules
            # (inherited from LLMProvider). Personality + voice contract
            # live in the prompt; the model follows them regardless of
            # which provider runs them.
            self.system_prompt = original_system + self._build_voice_suffix()

            # Voice mode: disable reasoning by default for low latency.
            self.reasoning_enabled = settings.VOICE_EXTENDED_THINKING
            self.reasoning_effort = settings.VOICE_THINKING_EFFORT

            voice_model = (settings.VOICE_OPENAI_MODEL or settings.VOICE_CLAUDE_MODEL or "").strip()
            if voice_model and voice_model.startswith(("gpt", "o1", "o3", "o4")):
                self.model = voice_model

            response = await self.generate_response(
                messages=messages, memory_text=memory_text,
            )
        finally:
            self.system_prompt = original_system
            self.reasoning_enabled = original_reasoning_enabled
            self.reasoning_effort = original_effort
            self.model = original_model

        if len(response) > max_chars:
            cut = response[:max_chars].rsplit(".", 1)[0]
            response = (cut + ".") if cut else response[:max_chars]
        return response

    # ------- Conversation analysis -------

    async def analyze_conversation(self, history_text: str, task: str) -> str:
        messages = [{
            "role": "user",
            "content": f"{task}\n\nConversation:\n{history_text}",
        }]
        return await self.generate_response(messages)


# Fallback system prompt when SYSTEM_PROMPT is not set and no personality
# system is wired up yet. Replaced in PR5 with the personality loader.
__all__ = ["OpenAIProvider"]
