"""Ollama provider — local self-hosted models, free, no API key.

Ollama (https://ollama.com) runs open-weight models on your own machine. The
server speaks the OpenAI Chat Completions protocol over HTTP, so we reuse the
`openai` SDK pointed at the local Ollama endpoint. No API key needed; the
default base URL is `http://localhost:11434/v1`.

Use this when:
- You want the bot to be 100% free (after hardware).
- You want privacy: messages never leave your machine.
- You want to experiment with open models (Llama 3.3, Qwen 3, DeepSeek R1,
  Mistral, etc.) without juggling provider keys.

Trade-offs vs cloud providers:
- You need to install Ollama and pull the model first (`ollama pull llama3.3`).
- Quality is slightly below Claude/GPT/Gemini for most use cases — but the
  gap is closing fast in 2026.
- Inference speed depends on your hardware (GPU >> CPU; Apple Silicon is fine
  for ~7B models).
"""

from __future__ import annotations

import re
from typing import Any

from config import settings

from .base import Capability, LLMProvider, resolve_personality, warn_if_mcp_configured


_URL_RE = re.compile(r"https?://\S+")


def _clean_err(e: Exception) -> str:
    text = str(e).split("\n")[0]
    text = _URL_RE.sub("", text)
    text = text.replace("`", "").strip()
    return text[:180] if text else type(e).__name__


def _is_reasoning_model(model: str) -> bool:
    """Whether this Ollama model accepts reasoning tokens.

    Heuristic: DeepSeek-R1, QwQ, and o1-style local clones have explicit
    reasoning. Most others don't. Models that don't will silently ignore
    a reasoning param.
    """
    m = model.lower()
    return (
        "deepseek-r1" in m
        or "qwq" in m
        or "reasoning" in m
        or m.startswith("o1")
    )


class OllamaProvider(LLMProvider):
    """Local Ollama instance via the OpenAI-compatible endpoint."""

    name = "ollama"

    def __init__(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError(
                "Ollama provider reuses the `openai` package. "
                "Install it with: pip install openai"
            ) from e

        # Ollama doesn't validate the api_key but the SDK requires one.
        # Use a dummy unless the user set something explicit.
        api_key = settings.OLLAMA_API_KEY or "ollama-no-auth"

        # Default to local Ollama. Allow override for remote/networked setups.
        base_url = settings.OLLAMA_BASE_URL or "http://localhost:11434/v1"

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = settings.OLLAMA_MODEL or "llama3.3"
        self.max_tokens = settings.MAX_TOKENS

        self._personality = resolve_personality(
            bot_name=settings.BOT_DISPLAY_NAME or "the bot",
        )
        self.system_prompt = self._personality.chat_prompt
        warn_if_mcp_configured(self.name)

        # Most local models are non-reasoning; flag is honoured only for
        # the few that support it.
        self.reasoning_enabled = settings.EXTENDED_THINKING
        self.reasoning_effort = settings.THINKING_EFFORT

    # ------- Capabilities -------

    def supports(self, capability: Capability) -> bool:
        # Ollama support depends heavily on the model. Advertise the
        # baseline guarantees; per-model gating is the caller's job if needed.
        return capability in {
            Capability.STREAMING,
            Capability.IMAGE_INPUT,   # multimodal models like llava, qwen2.5-vl
            Capability.REASONING,     # only for r1/qwq, ignored otherwise
        }

    # ------- Message translation -------

    @staticmethod
    def _translate_content(content: Any) -> Any:
        """Convert Anthropic-style content into OpenAI Chat Completions format.

        Same shape as OpenRouter — Ollama follows the OpenAI Chat schema.
        """
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
            elif btype == "document":
                # Ollama doesn't read PDFs natively; insert a marker.
                translated.append({
                    "type": "text",
                    "text": "[a PDF document was attached but this model can't read it natively]",
                })
            elif "text" in block:
                translated.append({"type": "text", "text": str(block["text"])})

        return translated or content

    @classmethod
    def _translate_messages(cls, messages: list[dict]) -> list[dict]:
        return [
            {"role": m.get("role", "user"), "content": cls._translate_content(m.get("content", ""))}
            for m in messages
        ]

    # ------- Prompt construction -------

    def _build_messages(self, messages: list[dict], memory_text: str = "") -> list[dict]:
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
                "model": self.model,
                "messages": self._build_messages(messages, memory_text),
                "max_tokens": self.max_tokens,
                "stream": True,
            }
            stream = await self.client.chat.completions.create(**create_kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    yield delta.content
        except Exception as e:
            yield (
                f"Ollama hiccupped. Is the daemon running on "
                f"{self.client.base_url}? ({_clean_err(e)})"
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
                "messages": self._build_messages(messages, memory_text),
                "max_tokens": self.max_tokens,
                "stream": True,
            }

            if self.reasoning_enabled and _is_reasoning_model(self.model):
                # Reasoning models in Ollama may accept `temperature` lowering
                # or explicit `extra_body={"reasoning": ...}`. The protocol
                # is still settling — for now we just pass through and let
                # the model decide.
                pass

            text_parts: list[str] = []
            stream = await self.client.chat.completions.create(**create_kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    text_parts.append(delta.content)

            return "".join(text_parts).strip()

        except Exception as e:
            return (
                f"Ollama hiccupped. Is the daemon running on "
                f"{self.client.base_url}? ({_clean_err(e)})"
            )

    # ------- Voice generation -------

    async def generate_voice_response(
        self,
        messages: list[dict],
        memory_text: str = "",
        max_chars: int | None = None,
    ) -> str:
        max_chars = max_chars or settings.VOICE_MAX_RESPONSE_CHARS

        original_system = self.system_prompt
        original_model = self.model

        try:
            self.system_prompt = original_system + self._build_voice_suffix()

            voice_model = (settings.VOICE_OLLAMA_MODEL or "").strip()
            if voice_model:
                self.model = voice_model

            response = await self.generate_response(
                messages=messages, memory_text=memory_text,
            )
        finally:
            self.system_prompt = original_system
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


__all__ = ["OllamaProvider"]
