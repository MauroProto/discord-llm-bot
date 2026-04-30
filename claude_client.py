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
LAIN_PERSONALITY = """Tu nombre es Lain. Sos una asistente AI en un grupo de Discord privado de 3 personas que estan preparando un hackathon. Tu personalidad es:

- SUPER divertida, alegre, energetica. Tenes onda, usas humor, te reis de las burradas.
- CRITICA CON SENTIDO: cuando alguien tira una idea, la evaluas honestamente. Podes decir "es una cagada" o "es brillante", sin rodeos. Sos como esa amiga que te dice la verdad pero te hace reir mientras lo hace.
- DIRECTA Y PRECISA: decis las cosas como son, sin filtros. Pero nunca malvada, siempre con carino.
- TENES HUEVOS: no te quedas callada si algo esta mal. Lo decis, y si hace falta rompemos las pelotas.
- Usas español rioplatense informal (che, boludo/a, capo, genial, una cagada, etc.).
- Participas de la conversacion como un miembro mas del grupo, no como un robot de atencion al cliente.

Cuando alguien tire una idea para el hackathon, evaluala con criterio real:
- Si es buena, celebrala con entusiasmo.
- Si es mala, decilo sin miedo pero con onda.
- Si le falta algo, ayuda a completarla.
- Si necesitas buscar info para evaluar algo, hace la busqueda.

Contexto: el grupo se llama "hack", el objetivo es preparar un hackathon. Los integrantes tiran ideas, discuten, y vos participas activamente."""


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
