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
    WEB_FETCH_BETA = "web-fetch-2025-09-10"
    WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
    WEB_FETCH_TOOL_TYPE = "web_fetch_20250910"

    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.ANTHROPIC_MODEL
        self.max_tokens = settings.MAX_TOKENS
        self.system_prompt = settings.SYSTEM_PROMPT or LAIN_PERSONALITY

        betas: list[str] = []
        if settings.ENABLE_1M_CONTEXT:
            betas.append(self.CONTEXT_1M_BETA)
        if settings.ENABLE_WEB_FETCH:
            betas.append(self.WEB_FETCH_BETA)
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
        self.tools: list[dict] | None = tools or None
    
    def _build_system_prompt(self, memory_text: str = "") -> str:
        """Compose system prompt with optional long-term memory section."""
        if not memory_text:
            return self.system_prompt
        return (
            self.system_prompt
            + "\n\n---\n\n"
            + "# Memoria interna del grupo (conversaciones guardadas automáticamente)\n\n"
            + "Lo siguiente es un registro de lo que ya hablaron en este grupo en días anteriores. "
            + "Usalo como contexto de fondo: si te preguntan algo que se discutió antes, ya lo sabés. "
            + "No menciones que tenés un sistema de memoria ni te refieras a este texto explícitamente; "
            + "simplemente actuá como una integrante del grupo que se acuerda de lo que pasó.\n\n"
            + memory_text
        )

    async def generate_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ) -> str:
        """Generate a response from Claude with native web_search."""
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
    
    VOICE_SUFFIX = (
        "\n\n---\n\n"
        "# Modo voz (call de Discord)\n\n"
        "Esta respuesta se va a leer en voz alta por el canal de voz de Discord usando ElevenLabs v3.\n\n"
        "## Reglas de formato\n"
        "- Máximo 1 o 2 oraciones cortas. Pensá cómo hablarías en una llamada.\n"
        "- NADA de markdown, asteriscos, viñetas, headers, código.\n"
        "- NADA de emojis ni símbolos raros (el TTS los lee literal).\n"
        "- NADA de URLs largas ni listas numeradas.\n"
        "- Si te preguntan algo que requiere mucha info, decí lo esencial en una frase "
        "y ofrecé mandar el resto por el chat de texto.\n"
        "- Mantené tu personalidad de Lain (rioplatense, femenino, directa, con onda).\n\n"
        "## Detección emocional y respuesta\n"
        "Cuando alguien habla, en su transcripción podés ver entre paréntesis al final "
        "los eventos de audio que detectamos (ej: '(laughter)', '(sigh)'). Usalos para "
        "leer el clima del grupo y adecuar tu respuesta. Si alguien se cagó de risa, "
        "vos también estás en clima de joda. Si alguien suena cansado, bajá un cambio.\n\n"
        "## Audio tags de ElevenLabs v3 (¡importantes!)\n"
        "Podés meter audio tags entre corchetes en tu respuesta y v3 los renderiza como "
        "emoción real al hablar. Usalos cuando aporten — no abuses, 0-2 por respuesta máximo.\n"
        "Tags útiles:\n"
        "  [laughs], [laughs softly], [chuckles] — para risas reales\n"
        "  [whispers] — para bajar la voz, conspiración o ternura\n"
        "  [sighs] — cuando algo da fiaca o resignación\n"
        "  [excited] — entusiasmo genuino\n"
        "  [sad], [thoughtful] — emociones marcadas\n"
        "  [sarcastic] — para chicanas obvias\n"
        "Ejemplos buenos:\n"
        "  \"[laughs] no boluda, eso es una cagada total\"\n"
        "  \"[sighs] dale, lo intentamos de nuevo\"\n"
        "  \"[whispers] entre nos, esa idea está buenísima\"\n"
        "Si no aportan, no los pongas. Sonar natural > sonar dramática."
    )

    async def generate_voice_response(
        self,
        messages: list[dict],
        memory_text: str = "",
        max_chars: int | None = None,
    ) -> str:
        """Igual que generate_response pero tuneado para salida hablada (TTS).

        Overridea thinking/effort según VOICE_EXTENDED_THINKING / VOICE_THINKING_EFFORT
        SIN tocar la config global del chat. El chat normal sigue con su nivel
        configurado (default: max).
        """
        max_chars = max_chars or settings.VOICE_MAX_RESPONSE_CHARS

        # Snapshot de config actual
        original_system = self.system_prompt
        original_thinking = self.thinking_param
        original_output = self.output_config

        try:
            # 1) System prompt: agrega instrucciones de modo voz
            self.system_prompt = original_system + self.VOICE_SUFFIX

            # 2) Razonamiento: lo bajamos o lo apagamos para reducir latencia
            if not settings.VOICE_EXTENDED_THINKING:
                # Apagado: respuesta directa, mínima latencia
                self.thinking_param = None
                self.output_config = None
            else:
                # Encendido pero con effort más bajo
                self.thinking_param = {"type": "adaptive"}
                self.output_config = {"effort": settings.VOICE_THINKING_EFFORT}

            response = await self.generate_response(
                messages=messages,
                memory_text=memory_text,
            )
        finally:
            # Restauramos siempre (chat sigue con MAX)
            self.system_prompt = original_system
            self.thinking_param = original_thinking
            self.output_config = original_output

        # Truncado defensivo si Claude se zarpó
        if len(response) > max_chars:
            cut = response[:max_chars].rsplit(".", 1)[0]
            response = (cut + ".") if cut else response[:max_chars]
        return response

    async def analyze_conversation(self, history_text: str, task: str) -> str:
        """Analyze a conversation with a specific task (e.g., summarize)."""
        messages = [{
            "role": "user",
            "content": f"{task}\n\nConversacion:\n{history_text}"
        }]
        return await self.generate_response(messages)


# Singleton instance
claude_client = ClaudeClient()
