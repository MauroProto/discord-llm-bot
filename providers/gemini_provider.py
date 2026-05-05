"""Google Gemini provider using the modern `google-genai` SDK.

Uses Google's `google.genai` package (NOT the deprecated
`google-generativeai`). The 2.5/3.x families expose `thinking_config` for
adjustable reasoning, native `google_search` as a tool, and accept image and
PDF inputs as `Part.from_bytes`.

Prompt caching: Gemini does implicit prefix caching automatically (no flags).
For very large stable contexts you can also use the explicit `caches.create`
API, but implicit is fine for our daily-memory window and avoids TTL
management.
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


def _effort_to_budget(effort: str) -> int:
    """Map our internal effort levels to Gemini `thinking_budget` token counts.

    Gemini accepts: -1 (dynamic — model decides), 0 (off), or a positive
    integer (cap on thinking tokens). We use:
      low    -> 1024
      medium -> 4096
      high   -> 16384
      xhigh  -> 32768
      max    -> -1 (let the model spend what it needs)
    """
    e = (effort or "").lower().strip()
    return {
        "low": 1024,
        "medium": 4096,
        "high": 16384,
        "xhigh": 32768,
        "max": -1,
    }.get(e, 4096)


class GeminiProvider(LLMProvider):
    """Google Gemini client via the `google-genai` SDK."""

    name = "gemini"

    def __init__(self) -> None:
        try:
            from google import genai  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "Gemini provider requires the `google-genai` package "
                "(NOT `google-generativeai`, which is deprecated). "
                "Install it with: pip install google-genai"
            ) from e

        api_key = settings.GOOGLE_API_KEY or settings.GEMINI_API_KEY
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY (or GEMINI_API_KEY) is required when "
                "LLM_PROVIDER=gemini. Get one at "
                "https://aistudio.google.com/apikey"
            )

        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.model = settings.GEMINI_MODEL or "gemini-2.5-pro"
        self.max_tokens = settings.MAX_TOKENS
        self._personality = resolve_personality(
            bot_name=settings.BOT_DISPLAY_NAME or "the bot",
        )
        self.system_prompt = self._personality.chat_prompt

        self.reasoning_enabled = settings.EXTENDED_THINKING
        self.reasoning_effort = settings.THINKING_EFFORT
        self.use_search = settings.ENABLE_WEB_SEARCH

    # ------- Capabilities -------

    def supports(self, capability: Capability) -> bool:
        return capability in {
            Capability.REASONING,    # 2.5 / 3.x have thinking_config
            Capability.WEB_SEARCH,   # via google_search tool
            Capability.IMAGE_INPUT,
            Capability.PDF_INPUT,
            Capability.PROMPT_CACHE, # implicit caching for stable prefixes
            Capability.CONTEXT_1M,   # 2.5 Flash and 3.x have 1M context
            Capability.STREAMING,
        }

    # ------- Message translation -------

    def _translate_content(self, content: Any) -> list:
        """Convert Anthropic-style content into Gemini Parts."""
        from google.genai import types  # type: ignore

        if isinstance(content, str):
            return [types.Part.from_text(text=content)]

        if not isinstance(content, list):
            return [types.Part.from_text(text=str(content))]

        parts: list = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(types.Part.from_text(text=str(block)))
                continue

            btype = block.get("type")

            if btype == "text":
                parts.append(types.Part.from_text(text=block.get("text", "")))

            elif btype == "image":
                source = block.get("source", {}) or {}
                if source.get("type") == "base64":
                    import base64
                    data = base64.b64decode(source.get("data", ""))
                    parts.append(types.Part.from_bytes(
                        data=data,
                        mime_type=source.get("media_type", "image/png"),
                    ))

            elif btype == "document":
                # Gemini accepts PDFs as Parts with the right mime type.
                source = block.get("source", {}) or {}
                if source.get("type") == "base64":
                    import base64
                    data = base64.b64decode(source.get("data", ""))
                    parts.append(types.Part.from_bytes(
                        data=data,
                        mime_type=source.get("media_type", "application/pdf"),
                    ))

            elif "text" in block:
                parts.append(types.Part.from_text(text=str(block["text"])))

        return parts

    def _translate_messages(self, messages: list[dict]) -> list:
        """Convert Anthropic-style messages into Gemini `contents`."""
        from google.genai import types  # type: ignore

        out: list = []
        for m in messages:
            role = m.get("role", "user")
            # Gemini uses 'model' instead of 'assistant'.
            gemini_role = "model" if role == "assistant" else "user"
            parts = self._translate_content(m.get("content", ""))
            out.append(types.Content(role=gemini_role, parts=parts))
        return out

    # ------- Prompt construction -------

    def _build_system_instruction(self, memory_text: str = "") -> str:
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

    def _build_config(self, memory_text: str) -> Any:
        from google.genai import types  # type: ignore

        config_kwargs: dict[str, Any] = {
            "system_instruction": self._build_system_instruction(memory_text),
            "max_output_tokens": self.max_tokens,
        }

        # Reasoning / thinking
        if self.reasoning_enabled:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=_effort_to_budget(self.reasoning_effort),
            )
        else:
            # Explicitly disable to keep latency predictable.
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

        # Native search tool
        if self.use_search:
            config_kwargs["tools"] = [
                types.Tool(google_search=types.GoogleSearch()),
            ]

        return types.GenerateContentConfig(**config_kwargs)

    # ------- Streaming chat generation -------

    async def stream_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ):
        try:
            contents = self._translate_messages(messages)
            config = self._build_config(memory_text)
            stream = await self.client.aio.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config,
            )
            async for chunk in stream:
                t = getattr(chunk, "text", None)
                if t:
                    yield t
        except Exception as e:
            yield f"Gemini hiccupped, retry me. ({_clean_err(e)})"

    # ------- Chat generation -------

    async def generate_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ) -> str:
        try:
            contents = self._translate_messages(messages)
            config = self._build_config(memory_text)

            text_parts: list[str] = []
            stream = await self.client.aio.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config,
            )
            async for chunk in stream:
                t = getattr(chunk, "text", None)
                if t:
                    text_parts.append(t)

            return "".join(text_parts).strip()

        except Exception as e:
            return f"Gemini hiccupped, retry me. ({_clean_err(e)})"

    # ------- Voice generation -------

    async def generate_voice_response(
        self,
        messages: list[dict],
        memory_text: str = "",
        max_chars: int | None = None,
    ) -> str:
        max_chars = max_chars or settings.VOICE_MAX_RESPONSE_CHARS

        original_system = self.system_prompt
        original_reasoning = self.reasoning_enabled
        original_effort = self.reasoning_effort
        original_model = self.model

        try:
            self.system_prompt = original_system + self._build_voice_suffix()

            self.reasoning_enabled = settings.VOICE_EXTENDED_THINKING
            self.reasoning_effort = settings.VOICE_THINKING_EFFORT

            voice_model = (
                settings.VOICE_GEMINI_MODEL
                or settings.VOICE_CLAUDE_MODEL
                or ""
            ).strip()
            if voice_model and voice_model.startswith("gemini"):
                self.model = voice_model

            response = await self.generate_response(
                messages=messages, memory_text=memory_text,
            )
        finally:
            self.system_prompt = original_system
            self.reasoning_enabled = original_reasoning
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


__all__ = ["GeminiProvider"]
