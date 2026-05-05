"""Configuration module using pydantic-settings for validation."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Config(BaseSettings):
    """Bot configuration loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Discord
    DISCORD_BOT_TOKEN: str = Field(..., description="Discord bot token")
    BOT_PREFIX: str = Field(default="!", description="Command prefix")
    ALLOWED_GUILD_ID: int | None = Field(
        default=None,
        description="Only respond in this guild ID (None = no restriction)"
    )
    ALLOWED_CHANNEL_ID: int | None = Field(
        default=None,
        description="Only respond in this channel ID (None = no restriction)"
    )
    
    # LLM provider selection (multi-provider support)
    LLM_PROVIDER: str = Field(
        default="anthropic",
        description=(
            "Which LLM provider to use. Currently supported: 'anthropic', "
            "'openai'. More providers (gemini, openrouter) coming in upcoming PRs."
        ),
    )

    # OpenAI (used when LLM_PROVIDER=openai or for OpenRouter base URL)
    OPENAI_API_KEY: str | None = Field(
        default=None,
        description="OpenAI API key (https://platform.openai.com/api-keys)",
    )
    OPENAI_MODEL: str = Field(
        default="gpt-5.4",
        description=(
            "OpenAI model id. Examples: gpt-5.4 (flagship), gpt-5.4-mini (cheap, "
            "fast), gpt-4.1 (1M context), o3 / o4-mini (reasoning models)."
        ),
    )
    OPENAI_BASE_URL: str | None = Field(
        default=None,
        description=(
            "Override the OpenAI API base URL. Use for OpenRouter "
            "(https://openrouter.ai/api/v1) or self-hosted OpenAI-compatible "
            "endpoints. Leave empty for OpenAI proper."
        ),
    )
    VOICE_OPENAI_MODEL: str | None = Field(
        default=None,
        description=(
            "Override OPENAI_MODEL specifically for voice replies (faster / "
            "cheaper). e.g. gpt-5.4-mini. Only honored when LLM_PROVIDER=openai."
        ),
    )

    # Claude / Anthropic
    ANTHROPIC_API_KEY: str = Field(..., description="Anthropic API key")
    ANTHROPIC_MODEL: str = Field(
        default="claude-opus-4-7",
        description="Claude model to use"
    )
    ENABLE_1M_CONTEXT: bool = Field(
        default=True,
        description="Enable 1M context window beta (Opus/Sonnet 4.x)"
    )
    EXTENDED_THINKING: bool = Field(
        default=True,
        description="Enable adaptive thinking (Claude Opus 4.7+)"
    )
    THINKING_EFFORT: str = Field(
        default="max",
        description="Reasoning effort: low | medium | high | xhigh | max"
    )
    MAX_TOKENS: int = Field(default=32000, description="Max tokens per response (output + thinking)")

    # Web search (Anthropic native server-side tool)
    ENABLE_WEB_SEARCH: bool = Field(
        default=True,
        description="Enable Anthropic native web_search tool (server-side)"
    )
    WEB_SEARCH_MAX_USES: int = Field(
        default=5,
        description="Max searches per turn"
    )

    # Web fetch (Anthropic native server-side tool, beta)
    ENABLE_WEB_FETCH: bool = Field(
        default=True,
        description="Enable Anthropic native web_fetch tool to read specific URLs"
    )
    WEB_FETCH_MAX_USES: int = Field(
        default=5,
        description="Max fetches per turn"
    )
    SYSTEM_PROMPT: str | None = Field(
        default=None,
        description="Override system prompt (None = use default Lain personality)"
    )
    
    # Context / Storage
    DATA_DIR: str = Field(default="./data", description="Directory for .md context files")
    HISTORY_LIMIT: int = Field(
        default=100,
        description="Number of messages to fetch from Discord history"
    )

    # Long-term memory: load recent .md context into prompt automatically
    MEMORY_DAYS: int = Field(
        default=14,
        description="Days of saved context to inject as long-term memory"
    )
    MEMORY_MAX_CHARS: int = Field(
        default=400_000,
        description="Cap on total characters loaded from .md memory"
    )
    
    # Web Search Fallback
    TAVILY_API_KEY: str | None = Field(
        default=None,
        description="Tavily API key for fallback search"
    )
    SERPAPI_KEY: str | None = Field(
        default=None,
        description="SerpAPI key for fallback search"
    )
    
    # Spontaneous responses (opt-in, disabled by default)
    SPONTANEOUS_RESPONSE: bool = Field(
        default=False,
        description="Bot responds without being mentioned"
    )
    SPONTANEOUS_PROBABILITY: float = Field(
        default=0.3,
        description="Probability of spontaneous response (0.0-1.0)"
    )
    
    # Health
    HEALTHCHECK_INTERVAL: int = Field(
        default=30,
        description="Healthcheck interval in seconds"
    )

    # Voice (ElevenLabs TTS + STT)
    VOICE_ENABLED: bool = Field(
        default=False,
        description="Master switch para funcionalidad de voz"
    )
    VOICE_CHANNEL_ID: int | None = Field(
        default=None,
        description=(
            "ID del canal de voz por defecto. Si está seteado, !join se conecta "
            "siempre a este canal (no importa dónde estés vos). Si es None, "
            "!join usa el canal de voz en el que estés conectado."
        )
    )
    ELEVENLABS_API_KEY: str | None = Field(
        default=None,
        description="API key de ElevenLabs (requerida si VOICE_ENABLED=true)"
    )
    ELEVENLABS_VOICE_ID: str = Field(
        default="AwmgI32PB22lsT7wnBFH",
        description="Voice ID de ElevenLabs (configurable en .env)"
    )
    ELEVENLABS_TTS_MODEL: str = Field(
        default="eleven_v3",
        description=(
            "Modelo TTS de ElevenLabs. Opciones: "
            "eleven_v3 (el más expresivo, recomendado por el usuario), "
            "eleven_turbo_v2_5 (balance calidad/latencia ~300ms, multilingüe), "
            "eleven_flash_v2_5 (ultra baja latencia ~75ms, calidad menor), "
            "eleven_multilingual_v2 (alta calidad, latencia ~1-2s)"
        )
    )
    ELEVENLABS_STT_MODEL: str = Field(
        default="scribe_v1",
        description="Modelo STT de ElevenLabs"
    )
    VOICE_LANGUAGE: str = Field(
        default="spa",
        description="Código ISO-639-3 del idioma para STT"
    )
    VOICE_ALWAYS_RESPOND: bool = Field(
        default=True,
        description="Si true, contesta a todo lo que oye en VC sin esperar wake word"
    )
    VOICE_WAKE_WORDS: str = Field(
        default="lain",
        description="CSV de wake words (solo se usan si VOICE_ALWAYS_RESPOND=false)"
    )
    VOICE_MIN_TURN_CHARS: int = Field(
        default=3,
        description="Ignora transcripts más cortos que esto (filtra ruido tipo 'eh', 'ah')"
    )
    VOICE_COOLDOWN_MS: int = Field(
        default=1500,
        description="Tiempo mínimo entre dos respuestas de Lain en VC"
    )
    VOICE_SILENCE_MS: int = Field(
        default=800,
        description="Silencio que cierra un turno de habla"
    )
    VOICE_MAX_TURN_SECONDS: int = Field(
        default=30,
        description="Máximo de segundos de un turno antes de forzar flush"
    )
    VOICE_MIRROR_TEXT: bool = Field(
        default=False,
        description="Si está en VC, leer también respuestas del canal de texto"
    )
    VOICE_IDLE_TIMEOUT_SECONDS: int = Field(
        default=120,
        description="Si no llega audio por este tiempo, Lain se va sola del VC. 0 = desactivado."
    )
    VOICE_IDLE_CHECK_SECONDS: int = Field(
        default=15,
        description="Cada cuántos segundos chequear el timeout de inactividad."
    )
    VOICE_MAX_RESPONSE_CHARS: int = Field(
        default=600,
        description="Truncar respuestas TTS más largas que esto"
    )
    VOICE_RECENT_TURNS: int = Field(
        default=30,
        description="Número de turnos de voz recientes a inyectar como contexto inmediato"
    )

    # Razonamiento de Claude en modo voz (independiente del chat)
    VOICE_EXTENDED_THINKING: bool = Field(
        default=False,
        description=(
            "Si false, Claude NO razona en modo voz (mínima latencia). "
            "Si true, usa VOICE_THINKING_EFFORT. El chat normal sigue con EXTENDED_THINKING/THINKING_EFFORT."
        )
    )
    VOICE_THINKING_EFFORT: str = Field(
        default="low",
        description="Esfuerzo de razonamiento solo para voz: low | medium | high | xhigh | max"
    )
    # Modelo Claude solo para voz (independiente del chat). Haiku es ~3-5x más rápido que Opus.
    VOICE_CLAUDE_MODEL: str = Field(
        default="claude-haiku-4-5",
        description="Modelo Claude solo para voz. Haiku=más rápido. Vacío=usa ANTHROPIC_MODEL"
    )
    # Cuánta memoria a largo plazo cargar en voz (en chars). 0 = ninguna (mucho más rápido).
    VOICE_MEMORY_MAX_CHARS: int = Field(
        default=0,
        description="Caracteres de memoria .md a inyectar en voz. 0=ninguno (rápido), -1=usa MEMORY_MAX_CHARS"
    )

    # ElevenLabs voice_settings (afectan cómo suena la voz)
    VOICE_SPEED: float = Field(
        default=0.92,
        description="Velocidad de habla. 1.0 = normal, <1 = más pausada, >1 = más rápida. Recomendado 0.88-0.95 para charla natural."
    )
    VOICE_STABILITY: float = Field(
        default=0.5,
        description="0-1. Mayor = voz más consistente y predecible; menor = más expresiva pero variable."
    )
    VOICE_SIMILARITY_BOOST: float = Field(
        default=0.8,
        description="0-1. Cuán fielmente la salida sigue el timbre de la voz original."
    )
    VOICE_STYLE: float = Field(
        default=0.15,
        description="0-1. Exageración estilística; un poco de style ayuda a que suene más natural y menos robot."
    )
    VOICE_USE_SPEAKER_BOOST: bool = Field(
        default=True,
        description="Amplifica la similitud con el hablante original."
    )


# Global config instance
settings = Config()
