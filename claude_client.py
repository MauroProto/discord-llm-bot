"""Claude client wrapper with Anthropic web search and fallback search."""

import anthropic
from anthropic import AsyncAnthropic
from config import settings
from search_client import search_client


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

        self.thinking_param: dict | None = None
        if settings.EXTENDED_THINKING:
            budget = max(1024, min(settings.THINKING_BUDGET_TOKENS, self.max_tokens - 1024))
            self.thinking_param = {"type": "enabled", "budget_tokens": budget}

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
            if self.tools:
                create_kwargs["tools"] = self.tools

            response = await self.client.messages.create(**create_kwargs)

            # Concatenate all text blocks (skip thinking & tool blocks)
            parts = [
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", None)
            ]
            return "\n".join(parts).strip()
            
        except anthropic.APIError as e:
            return f"Che, me quede sin creditos o la API de Claude esta caida. Error: {str(e)[:100]}"
        except Exception as e:
            return f"Ups, me rompi algo interno. Reintentame en un toque. Error: {str(e)[:100]}"
    
    async def analyze_conversation(self, history_text: str, task: str) -> str:
        """Analyze a conversation with a specific task (e.g., summarize)."""
        messages = [{
            "role": "user",
            "content": f"{task}\n\nConversacion:\n{history_text}"
        }]
        return await self.generate_response(messages)


# Singleton instance
claude_client = ClaudeClient()
