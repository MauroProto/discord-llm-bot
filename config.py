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
            "Which LLM provider to use. Supported: 'anthropic', 'openai', "
            "'gemini', 'openrouter', 'ollama'. Use 'openrouter' for one key "
            "across many cloud models, or 'ollama' to run open-weight models "
            "locally for free."
        ),
    )

    # Personality / display name
    BOT_PERSONALITY: str = Field(
        default="friendly",
        description=(
            "Which personality preset from `personalities/` to load. Built-in: "
            "'friendly', 'snarky', 'analyst'. Add your own .md file in that "
            "directory and reference it by id here."
        ),
    )
    BOT_DISPLAY_NAME: str | None = Field(
        default=None,
        description=(
            "Substituted for {{BOT_NAME}} in personality prompts. If empty, "
            "the bot's actual Discord username is used at runtime."
        ),
    )
    LEGACY_SELF_PREFIXES: str | None = Field(
        default=None,
        description=(
            "Optional CSV of historic prefixes the bot may have used in its "
            "own past messages (e.g. when you renamed the bot). The history "
            "builder strips them when re-feeding the conversation to the LLM. "
            "Example: 'OldName:,OldName-bot:'."
        ),
    )
    CUSTOM_SYSTEM_PROMPT: str | None = Field(
        default=None,
        description=(
            "Override BOT_PERSONALITY entirely with a literal system prompt. "
            "Useful when the prompt is private and you don't want it in git."
        ),
    )
    CUSTOM_SYSTEM_PROMPT_FILE: str | None = Field(
        default=None,
        description=(
            "Path to a personality file to load instead of BOT_PERSONALITY. "
            "Useful for prompts too long for an env var, or for keeping the "
            "file outside this repo."
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

    # Google Gemini (used when LLM_PROVIDER=gemini)
    GOOGLE_API_KEY: str | None = Field(
        default=None,
        description=(
            "Google AI Studio API key (https://aistudio.google.com/apikey). "
            "Used by the Gemini provider. `GEMINI_API_KEY` is accepted as an alias."
        ),
    )
    GEMINI_API_KEY: str | None = Field(
        default=None,
        description="Alias for GOOGLE_API_KEY for users who prefer that name.",
    )
    GEMINI_MODEL: str = Field(
        default="gemini-2.5-pro",
        description=(
            "Gemini model id. Examples: gemini-2.5-pro (smart), "
            "gemini-2.5-flash (fast/cheap, 1M context), gemini-2.5-flash-lite "
            "(fastest), gemini-3.1-pro-preview (latest flagship)."
        ),
    )
    VOICE_GEMINI_MODEL: str | None = Field(
        default=None,
        description=(
            "Override GEMINI_MODEL specifically for voice replies. "
            "e.g. gemini-2.5-flash. Only honored when LLM_PROVIDER=gemini."
        ),
    )

    # OpenRouter (used when LLM_PROVIDER=openrouter — one key for many models)
    OPENROUTER_API_KEY: str | None = Field(
        default=None,
        description="OpenRouter API key (https://openrouter.ai/keys).",
    )
    OPENROUTER_MODEL: str = Field(
        default="anthropic/claude-haiku-4-5",
        description=(
            "OpenRouter model id, format 'provider/model'. Examples: "
            "anthropic/claude-opus-4-7, openai/gpt-5.4, google/gemini-3.1-pro-preview, "
            "meta-llama/llama-3.3-70b-instruct. Append ':online' to enable web search."
        ),
    )
    OPENROUTER_REFERER: str | None = Field(
        default=None,
        description=(
            "Optional HTTP-Referer header sent to OpenRouter so your app "
            "appears in their dashboard. e.g. https://yourdomain.example."
        ),
    )
    OPENROUTER_APP_NAME: str | None = Field(
        default=None,
        description="Optional X-Title header (your app name) for OpenRouter dashboard.",
    )
    VOICE_OPENROUTER_MODEL: str | None = Field(
        default=None,
        description=(
            "Override OPENROUTER_MODEL specifically for voice replies. "
            "e.g. anthropic/claude-haiku-4-5 or openai/gpt-5.4-mini."
        ),
    )

    # Ollama (used when LLM_PROVIDER=ollama — local self-hosted)
    OLLAMA_BASE_URL: str | None = Field(
        default=None,
        description=(
            "Ollama server URL. Defaults to http://localhost:11434/v1 if empty. "
            "Set this when Ollama runs on another machine in your network."
        ),
    )
    OLLAMA_API_KEY: str | None = Field(
        default=None,
        description=(
            "Ollama doesn't authenticate by default. Only set this if you put "
            "Ollama behind a reverse proxy that requires a bearer token."
        ),
    )
    OLLAMA_MODEL: str = Field(
        default="llama3.3",
        description=(
            "Ollama model id. Examples: llama3.3, qwen3:32b, deepseek-r1:14b, "
            "mistral, llava (multimodal). Pull it first with `ollama pull <id>`."
        ),
    )
    VOICE_OLLAMA_MODEL: str | None = Field(
        default=None,
        description=(
            "Override OLLAMA_MODEL for voice replies. e.g. llama3.2:3b for "
            "lower latency on the voice path."
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
        description=(
            "[deprecated] Legacy alias of CUSTOM_SYSTEM_PROMPT. Still honored "
            "for backwards compatibility. Prefer CUSTOM_SYSTEM_PROMPT in new "
            "deployments — the personality system replaces this."
        ),
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
        default="bot",
        description="CSV de wake words (solo se usan si VOICE_ALWAYS_RESPOND=false)"
    )
    VOICE_MIN_TURN_CHARS: int = Field(
        default=3,
        description="Ignora transcripts más cortos que esto (filtra ruido tipo 'eh', 'ah')"
    )
    VOICE_COOLDOWN_MS: int = Field(
        default=1500,
        description="Min ms between two consecutive bot replies in VC."
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
        description="Auto-leave the VC if no audio arrives for this long (seconds). 0 disables."
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
