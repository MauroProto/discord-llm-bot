"""OpenRouter provider — one key, 100+ models.

OpenRouter (https://openrouter.ai) speaks the OpenAI Chat Completions
protocol against a unified catalog: Claude, GPT, Gemini, Llama, Mistral,
DeepSeek, etc. Pricing is passthrough plus a small margin, which is usually
much cheaper than maintaining three separate provider subscriptions.

Model ids look like `<provider>/<model>` — e.g. `anthropic/claude-opus-4-7`,
`openai/gpt-5.4`, `google/gemini-3.1-pro-preview`. Append `:online` to a
model id (e.g. `anthropic/claude-opus-4-7:online`) to enable native web
search through OpenRouter's search shim.

We reuse the `openai` Python SDK pointed at OpenRouter's base URL because
the Chat Completions API is the lowest common denominator and works across
every model. Reasoning effort is forwarded via the `extra_body` kwarg when
the model supports it.
"""

from __future__ import annotations

import re
from typing import Any

from config import settings

from .base import Capability, LLMProvider, resolve_personality


_URL_RE = re.compile(r"https?://\S+")
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _clean_err(e: Exception) -> str:
    text = str(e).split("\n")[0]
    text = _URL_RE.sub("", text)
    text = text.replace("`", "").strip()
    return text[:180] if text else type(e).__name__


def _is_reasoning_model(model: str) -> bool:
    """Whether this OpenRouter model accepts the `reasoning` param."""
    m = model.lower()
    return (
        "claude-opus" in m or "claude-sonnet-4" in m  # Anthropic 4.x with thinking
        or m.startswith("openai/o") or "openai/gpt-5" in m  # OpenAI o-series + GPT-5
        or "/gemini-2.5" in m or "/gemini-3" in m  # Gemini 2.5+
        or m.startswith("o") and any(c.isdigit() for c in m[:3])  # bare o3, o4 etc.
    )


def _map_effort(effort: str) -> str:
    """OpenRouter normalises reasoning effort to low/medium/high."""
    e = (effort or "").lower().strip()
    if e in {"low", "medium", "high"}:
        return e
    if e in {"xhigh", "max"}:
        return "high"
    return "medium"


class OpenRouterProvider(LLMProvider):
    """OpenAI-compatible client pointed at OpenRouter."""

    name = "openrouter"

    def __init__(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError(
                "OpenRouter provider requires the `openai` package. "
                "Install it with: pip install openai"
            ) from e

        if not settings.OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter. "
                "Get one at https://openrouter.ai/keys"
            )

        # OpenRouter recommends sending HTTP-Referer and X-Title headers so
        # apps appear in their dashboard. Optional but nice to have.
        default_headers: dict[str, str] = {}
        if settings.OPENROUTER_REFERER:
            default_headers["HTTP-Referer"] = settings.OPENROUTER_REFERER
        if settings.OPENROUTER_APP_NAME:
            default_headers["X-Title"] = settings.OPENROUTER_APP_NAME

        self.client = AsyncOpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url=_OPENROUTER_BASE_URL,
            default_headers=default_headers or None,
        )

        self.model = settings.OPENROUTER_MODEL or "anthropic/claude-haiku-4-5"
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
        # OpenRouter capabilities depend on the chosen model. We advertise
        # broadly here; per-model gating is the caller's job if needed.
        return capability in {
            Capability.REASONING,
            Capability.WEB_SEARCH,    # via `:online` suffix or built-in tool routing
            Capability.IMAGE_INPUT,
            Capability.PDF_INPUT,
            Capability.PROMPT_CACHE,  # passthrough — works for Anthropic models
            Capability.CONTEXT_1M,    # available on supported models
            Capability.STREAMING,
        }

    # ------- Message translation -------

    @staticmethod
    def _translate_content(content: Any) -> Any:
        """Convert Anthropic-style content into OpenAI Chat Completions format."""
        if isinstance(content, str):
            return content

        if not isinstance(content, list):
            return str(content)

        translated: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                translated.append({"type": "text", "text": str(block)})
                continue

            btype = block.get("type")

            if btype == "text":
                translated.append({"type": "text", "text": block.get("text", "")})

            elif btype == "image":
                source = block.get("source", {}) or {}
                if source.get("type") == "base64":
                    media = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    translated.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media};base64,{data}"},
                    })
                elif source.get("type") == "url":
                    translated.append({
                        "type": "image_url",
                        "image_url": {"url": source.get("url", "")},
                    })

            elif btype == "document":
                # Chat Completions has no native PDF input. Fall back to a
                # text marker so the model knows a file was attached, and let
                # the user know to use a multimodal-capable provider for PDFs.
                translated.append({
                    "type": "text",
                    "text": "[a PDF document was attached but this model can't read it natively]",
                })

            elif "text" in block:
                translated.append({"type": "text", "text": str(block["text"])})

        return translated or content

    @classmethod
    def _translate_messages(cls, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            role = m.get("role", "user")
            out.append({"role": role, "content": cls._translate_content(m.get("content", ""))})
        return out

    # ------- Prompt construction -------

    def _build_messages(self, messages: list[dict], memory_text: str = "") -> list[dict]:
        """Prepend a system message that combines personality + long-term
        memory. OpenRouter / OpenAI Chat Completions doesn't have an
        `instructions` field — the system role is the convention."""
        system_content = self.system_prompt
        if memory_text:
            system_content += (
                "\n\n---\n\n"
                "# Long-term memory (auto-saved conversations)\n\n"
                "The following is a record of what was discussed in previous sessions. "
                "Use it as background context. Do not mention this memory system or "
                "reference this text explicitly — just behave like a member of the "
                "group who remembers what happened.\n\n"
                + memory_text
            )

        return [
            {"role": "system", "content": system_content},
            *self._translate_messages(messages),
        ]

    def _resolve_model(self) -> str:
        """Append the `:online` suffix when web search is requested and the
        model id doesn't already include it."""
        m = self.model
        if self.use_search and ":online" not in m:
            return f"{m}:online"
        return m

    # ------- Streaming chat generation -------

    async def stream_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ):
        try:
            create_kwargs: dict[str, Any] = {
                "model": self._resolve_model(),
                "messages": self._build_messages(messages, memory_text),
                "max_tokens": self.max_tokens,
                "stream": True,
            }
            if self.reasoning_enabled and _is_reasoning_model(self.model):
                create_kwargs["extra_body"] = {
                    "reasoning": {"effort": _map_effort(self.reasoning_effort)},
                }

            stream = await self.client.chat.completions.create(**create_kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    yield delta.content
        except Exception as e:
            yield f"OpenRouter hiccupped, retry me. ({_clean_err(e)})"

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
                "model": self._resolve_model(),
                "messages": self._build_messages(messages, memory_text),
                "max_tokens": self.max_tokens,
                "stream": True,
            }

            if self.reasoning_enabled and _is_reasoning_model(self.model):
                # OpenRouter accepts `reasoning` in extra_body for compatible
                # models; non-supporting models ignore it.
                create_kwargs["extra_body"] = {
                    "reasoning": {"effort": _map_effort(self.reasoning_effort)},
                }

            text_parts: list[str] = []
            stream = await self.client.chat.completions.create(**create_kwargs)
            async for chunk in stream:
                # ChatCompletionChunk.choices[0].delta.content — incremental
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    text_parts.append(delta.content)

            return "".join(text_parts).strip()

        except Exception as e:
            return f"OpenRouter hiccupped, retry me. ({_clean_err(e)})"

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
                settings.VOICE_OPENROUTER_MODEL
                or settings.VOICE_CLAUDE_MODEL
                or ""
            ).strip()
            if voice_model:
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


__all__ = ["OpenRouterProvider"]
