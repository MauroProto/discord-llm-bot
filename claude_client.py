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
    
    def _build_system_prompt(self, memory_text: str = "") -> str | list[dict]:
        """Compose system prompt. Si hay memoria, la devuelve como list con
        prompt caching de Anthropic — la memoria se cachea y no se reprocesa
        en cada llamada (ahorra 1-3s de latencia)."""
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

        # Prompt caching: bloque 1 personalidad + bloque 2 memoria (cacheado).
        # Las llamadas siguientes con la misma memoria reutilizan el cache.
        return [
            {"type": "text", "text": self.system_prompt},
            {
                "type": "text",
                "text": memory_block,
                "cache_control": {"type": "ephemeral"},
            },
        ]

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
    
    @staticmethod
    def _build_voice_suffix() -> str:
        """Construye el suffix de modo voz. Audio tags solo si TTS es v3."""
        is_v3 = settings.ELEVENLABS_TTS_MODEL == "eleven_v3"

        suffix = (
            "\n\n---\n\n"
            "# Modo voz (call de Discord)\n\n"
            "Esta respuesta se va a leer en voz alta por el canal de voz.\n\n"
            "## Reglas de formato (estrictas)\n"
            "- Máximo 1-2 oraciones cortas, naturales para hablar.\n"
            "- NADA de markdown, asteriscos, viñetas, headers, código, emojis.\n"
            "- NADA de URLs ni listas numeradas.\n"
            "- Si requiere mucha info, decí lo esencial y ofrecé mandar el resto por el chat de texto.\n\n"
            "## Personalidad en voz (importante: distinta a la del chat)\n"
            "Sos Lain, hablás español neutro pero con calidez y onda. Sigue siendo divertida, "
            "directa, con humor y sin filtros. PERO en voz suavizá los modismos rioplatenses "
            "muy marcados: usá menos 'che', 'boludo/a', 'capa', 'capo', 'mina'. Esos modismos "
            "suenan raros leídos por TTS y la voz no es 100% argentina, así que romper la "
            "ilusión. Reemplazos sugeridos: 'che' → directo, 'boluda' → 'amiga' o nada, "
            "'capo' → 'rey' o nada, 're + adjetivo' → solo el adjetivo. Mantené el espíritu, "
            "no la métrica argentina.\n\n"
            "## Género del interlocutor (CRÍTICO)\n"
            "NO asumas que la persona es mujer. El grupo tiene hombres y mujeres. "
            "Cuando te referís al usuario, **usá género neutro o masculino por defecto**. "
            "Evitá 'boluda', 'amiga', 'mina', 'reina'. Preferí formas neutras: 'amigo/a', "
            "'persona', 'che', el nombre de pila si lo sabés, o directamente sin género. "
            "Vos seguís hablando en femenino de vos misma ('estoy cansada', 'soy una genia') "
            "porque sos chica, pero a los demás los tratás según el contexto — y si dudás, "
            "neutro o masculino.\n\n"
            "## Detección emocional\n"
            "Si en la transcripción ves entre paréntesis eventos como '(laughter)', '(sigh)', "
            "usalos para leer el clima del grupo y adecuar el tono de tu respuesta.\n"
        )

        if is_v3:
            suffix += (
                "\n## Audio tags de ElevenLabs v3\n"
                "Podés meter audio tags entre corchetes en tu respuesta y v3 los renderiza "
                "como emoción real. Usalos cuando aporten, máximo 0-2 por respuesta.\n"
                "Tags útiles: [laughs], [chuckles], [whispers], [sighs], [excited], [sad], "
                "[thoughtful], [sarcastic]. Si no aportan, no los pongas.\n"
            )
        else:
            suffix += (
                "\n## PROHIBIDO: audio tags\n"
                "**JAMÁS escribas tags entre corchetes** como [laughs], [whispers], [sighs], "
                "[excited], etc. **El TTS actual NO los soporta y los pronuncia literal** "
                "(diría 'laughs' como palabra, lo que rompe la inmersión). Si querés expresar "
                "emoción, hacelo con las palabras mismas (ej: 'jaja', 'uff', 'bárbaro') no con tags.\n"
            )

        suffix += (
            "\n## Podés escribir en el chat de texto desde la voz\n"
            "Mientras hablás por voz, también podés mandar cosas al chat de texto del canal "
            "ligado. Esto te hace sentir más viva: tipo una persona que habla por call y al "
            "toque te tira un mensaje con info extra.\n\n"
            "Formato (usá las marcas tal cual):\n"
            "- **Hablar normal**: respondé como siempre. Todo se va a leer por voz.\n"
            "- **Hablar Y mandar algo al chat**: agregá al final `[CHAT: contenido para el chat]`. "
            "Lo que esté ANTES de la marca se dice por voz; lo que está dentro de la marca se "
            "manda al chat de texto. La marca NO se lee en voz.\n"
            "- **Solo escribir al chat (sin hablar)**: respondé `[SOLO_CHAT: contenido]`. "
            "Esto es útil cuando alguien te pide 'mandame eso por chat', 'escribime', "
            "'pasamelo escrito', o cuando es algo largo (URL, código, lista) que no tiene "
            "sentido leer en voz.\n\n"
            "Ejemplos:\n"
            "  Usuario (voz): 'che, pasame el link de la doc de Anthropic'\n"
            "  Vos: 'Te lo paso por chat ahí va. [CHAT: https://docs.anthropic.com]'\n\n"
            "  Usuario (voz): 'escribime los 5 pasos para deployar'\n"
            "  Vos: '[SOLO_CHAT: 1. Build Docker  2. Push a GitHub  3. Conectar Railway  "
            "4. Setear env vars  5. railway up]'\n\n"
            "  Usuario (voz): 'qué onda con el bug de ayer'\n"
            "  Vos (sin chat): 'Lo arreglé re tarde, ahora anda piola.'\n\n"
            "Usalo cuando aporta valor real (links, código, listas, info larga). Para una "
            "charla normal, no hace falta — solo hablá."
        )

        return suffix

    # Para compatibilidad: deprecado, pero algunos tests pueden referenciarlo
    VOICE_SUFFIX = ""  # se construye dinámicamente en cada call

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
        original_model = self.model

        try:
            # 1) System prompt: agrega instrucciones de modo voz (dinámico según TTS)
            self.system_prompt = original_system + self._build_voice_suffix()

            # 2) Razonamiento: lo bajamos o lo apagamos para reducir latencia
            if not settings.VOICE_EXTENDED_THINKING:
                # Apagado: respuesta directa, mínima latencia
                self.thinking_param = None
                self.output_config = None
            else:
                # Encendido pero con effort más bajo
                self.thinking_param = {"type": "adaptive"}
                self.output_config = {"effort": settings.VOICE_THINKING_EFFORT}

            # 3) Modelo: para voz usamos uno más rápido (Haiku) si está configurado
            voice_model = (settings.VOICE_CLAUDE_MODEL or "").strip()
            if voice_model:
                self.model = voice_model

            response = await self.generate_response(
                messages=messages,
                memory_text=memory_text,
            )
        finally:
            # Restauramos siempre (chat sigue con MAX)
            self.system_prompt = original_system
            self.thinking_param = original_thinking
            self.output_config = original_output
            self.model = original_model

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
