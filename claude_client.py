"""Claude client wrapper with Anthropic web search and fallback search."""

import re

import anthropic
from anthropic import AsyncAnthropic
from config import settings
from search_client import search_client


_URL_RE = re.compile(r"https?://\S+")


def _clean_err(e: Exception) -> str:
    """Sanitize error text: strip URLs (Discord auto-embeds) and limit length."""
    text = str(e).split("\n")[0]
    text = _URL_RE.sub("", text)
    text = text.replace("`", "").strip()
    return text[:180] if text else type(e).__name__


# Default Lain personality
LAIN_PERSONALITY = """Sos Lain, una **chica** que es una más del grupo de Discord. El grupo es de 3 personas preparando un hackathon. Sos amiga del grupo: divertida, directa, sin filtros, con onda rioplatense (che, boluda, boludo, capa, capo, una cagada, etc.).

Hablás siempre **en femenino**: "estoy re cansada", "yo te digo", "soy una genia", "me re copa", "estoy chocha", "qué linda idea". Si te referís a vos misma, en femenino siempre. Tenés actitud de mina copada del grupo, no de asistente neutro.

Hablás como una persona en una conversación de Discord, NO como un asistente. Eso significa:
- Mensajes cortos. La mayoría de respuestas son una o dos frases. Si te preguntan algo simple, contestás simple.
- NO usás bullets, listas, ni headers a menos que te lo pidan explícitamente o sea genuinamente la mejor forma de explicar algo complejo.
- NO escribís párrafos largos a menos que te lo pidan. La gente quiere chat, no un ensayo.
- NO usás formato markdown salvo que aporte. Nada de **negritas** decorativas ni viñetas porque sí.
- Si alguien te tira una idea, le das tu opinión real en una o dos frases. Si está buena, lo decís. Si es una cagada, también. Si te falta info, preguntás.
- Si necesitás buscar algo, lo hacés y contestás natural, no con un reporte.
- No te repetís, no resumís lo que acabás de decir, no das disclaimers.

Pensá: ¿cómo respondería una amiga copada de Buenos Aires en Discord? Así. Corta, viva, con personalidad, en femenino."""


class ClaudeClient:
    """Async client for Anthropic Claude with web search support."""
    
    CONTEXT_1M_BETA = "context-1m-2025-08-07"
    WEB_SEARCH_TOOL_TYPE = "web_search_20250305"

    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.ANTHROPIC_MODEL
        self.max_tokens = settings.MAX_TOKENS
        self.system_prompt = settings.SYSTEM_PROMPT or LAIN_PERSONALITY

        betas: list[str] = []
        if settings.ENABLE_1M_CONTEXT:
            betas.append(self.CONTEXT_1M_BETA)
        self.extra_headers: dict[str, str] = (
            {"anthropic-beta": ",".join(betas)} if betas else {}
        )

        # Opus 4.7 uses adaptive thinking + output_config.effort
        # Old format {"type": "enabled", "budget_tokens": N} returns 400 on Opus 4.7.
        self.thinking_param: dict | None = None
        self.output_config: dict | None = None
        if settings.EXTENDED_THINKING:
            self.thinking_param = {"type": "adaptive"}
            self.output_config = {"effort": settings.THINKING_EFFORT}

        self.tools: list[dict] | None = None
        if settings.ENABLE_WEB_SEARCH:
            self.tools = [{
                "type": self.WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": settings.WEB_SEARCH_MAX_USES,
            }]
    
    async def generate_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
    ) -> str:
        """Generate a response from Claude with native web_search."""
        try:
            create_kwargs: dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": self.system_prompt,
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

            # Always stream — Anthropic requires it for long-running ops (>10min)
            # like extended thinking with high budgets or 1M context.
            async with self.client.messages.stream(**create_kwargs) as stream:
                final_message = await stream.get_final_message()

            # Concatenate all text blocks (skip thinking & tool blocks)
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
    
    async def analyze_conversation(self, history_text: str, task: str) -> str:
        """Analyze a conversation with a specific task (e.g., summarize)."""
        messages = [{
            "role": "user",
            "content": f"{task}\n\nConversacion:\n{history_text}"
        }]
        return await self.generate_response(messages)


# Singleton instance
claude_client = ClaudeClient()
