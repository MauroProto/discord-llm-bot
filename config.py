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
    STREAMING_REPLIES: bool = Field(
        default=True,
        description=(
            "Stream LLM responses to Discord by editing the message in place "
            "as tokens arrive. Lower perceived latency. Disable if your LLM "
            "or Discord rate limits cause issues."
        ),
    )
    STREAM_EDIT_INTERVAL_MS: int = Field(
        default=600,
        description=(
            "Min ms between Discord message edits during streaming. Discord "
            "rate-limits ~5 edits/sec; 600ms keeps us safely under that."
        ),
    )
    STREAM_EDIT_MIN_DELTA_CHARS: int = Field(
        default=20,
        description=(
            "Don't edit the streamed Discord message unless at least this many "
            "new characters have accumulated since the last edit."
        ),
    )

    LLM_PROVIDER: str = Field(
        default="anthropic",
        description=(
            "Which LLM provider to use. Supported: 'anthropic', 'openai', "
            "'gemini', 'openrouter', 'ollama', 'codex_cli'. Use 'openrouter' "
            "for one key across many cloud models, 'ollama' for open-weight "
            "models locally, or 'codex_cli' to use your ChatGPT Plus / Pro "
            "subscription quota via the local `codex` CLI binary."
        ),
    )

    # MCP (Model Context Protocol) — remote tool servers
    MCP_SERVERS_JSON: str | None = Field(
        default=None,
        description=(
            "JSON array of MCP servers to expose as tools. Each entry is "
            "{name, url, authorization_token?}. Anthropic's Messages API "
            "executes them server-side (beta `mcp-client-2025-11-20`); "
            "other providers will warn and ignore. Example: "
            "'[{\"name\":\"github\",\"url\":\"https://mcp.github.com/sse\","
            "\"authorization_token\":\"ghp_...\"}]'"
        ),
    )
    MCP_TOOL_FILTERS_JSON: str | None = Field(
        default=None,
        description=(
            "Optional JSON map of per-server tool allow/deny lists, e.g. "
            "'{\"github\":{\"deny\":[\"delete_repo\"]}}'. Allowlist mode "
            "(`allow`) disables everything else; denylist (`deny`) keeps "
            "everything else enabled."
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

    # Codex CLI (used when LLM_PROVIDER=codex_cli — uses your ChatGPT subscription)
    CODEX_CLI_BIN: str | None = Field(
        default=None,
        description=(
            "Path to the `codex` CLI binary. Defaults to whatever `which codex` "
            "returns. Install with `npm install -g @openai/codex` and run "
            "`codex login` (or `codex login --device-auth` on a headless box) "
            "before starting the bot."
        ),
    )
    CODEX_CLI_MODEL: str = Field(
        default="gpt-5-codex",
        description=(
            "Model to request from the Codex CLI (passed via -m). Examples: "
            "gpt-5-codex (default), o3, gpt-5.4. Subject to your subscription "
            "tier's allowed models."
        ),
    )
    CODEX_CLI_WORKDIR: str | None = Field(
        default=None,
        description=(
            "Working directory the Codex CLI runs in. Defaults to the bot's "
            "process cwd. Most chat use doesn't depend on this."
        ),
    )
    VOICE_CODEX_CLI_MODEL: str | None = Field(
        default=None,
        description="Override CODEX_CLI_MODEL specifically for voice replies.",
    )

    # Claude / Anthropic
    ANTHROPIC_API_KEY: str = Field(
        default="",
        description=(
            "Anthropic API key. Required only when LLM_PROVIDER=anthropic; "
            "the Anthropic provider raises a clear error at startup if missing."
        ),
    )
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
            "Default voice channel ID. If set, !join always connects here "
            "regardless of where you are. If None, !join uses whichever "
            "voice channel you're currently in."
        )
    )
    ELEVENLABS_API_KEY: str | None = Field(
        default=None,
        description="ElevenLabs API key (required if VOICE_ENABLED=true)"
    )
    ELEVENLABS_VOICE_ID: str = Field(
        default="AwmgI32PB22lsT7wnBFH",
        description="ElevenLabs voice ID (configurable via .env)"
    )
    ELEVENLABS_TTS_MODEL: str = Field(
        default="eleven_v3",
        description=(
            "ElevenLabs TTS model. Options: "
            "eleven_v3 (most expressive, recommended), "
            "eleven_turbo_v2_5 (quality/latency balance ~300ms, multilingual), "
            "eleven_flash_v2_5 (ultra-low latency ~75ms, lower quality), "
            "eleven_multilingual_v2 (high quality, ~1-2s latency)"
        )
    )
    ELEVENLABS_STT_MODEL: str = Field(
        default="scribe_v1",
        description="Modelo STT de ElevenLabs"
    )
    VOICE_LANGUAGE: str = Field(
        default="spa",
        description="ISO-639-3 language code for STT"
    )
    VOICE_ALWAYS_RESPOND: bool = Field(
        default=True,
        description="If true, reply to everything heard in VC without waiting for a wake word"
    )
    VOICE_WAKE_WORDS: str = Field(
        default="bot",
        description="CSV of wake words (only used if VOICE_ALWAYS_RESPOND=false)"
    )
    VOICE_MIN_TURN_CHARS: int = Field(
        default=3,
        description="Ignore transcripts shorter than this (filters noise like 'uh', 'eh')"
    )
    VOICE_COOLDOWN_MS: int = Field(
        default=1500,
        description="Min ms between two consecutive bot replies in VC."
    )
    VOICE_SILENCE_MS: int = Field(
        default=800,
        description="Silence that closes a speaking turn"
    )
    VOICE_MAX_TURN_SECONDS: int = Field(
        default=30,
        description="Max seconds of a turn before forcing a flush"
    )
    VOICE_MIRROR_TEXT: bool = Field(
        default=False,
        description="If in a VC, also read out replies from the text channel"
    )
    VOICE_IDLE_TIMEOUT_SECONDS: int = Field(
        default=120,
        description="Auto-leave the VC if no audio arrives for this long (seconds). 0 disables."
    )
    VOICE_IDLE_CHECK_SECONDS: int = Field(
        default=15,
        description="How often (seconds) to check the idle timeout."
    )
    VOICE_MAX_RESPONSE_CHARS: int = Field(
        default=600,
        description="Truncate TTS responses longer than this"
    )
    VOICE_RECENT_TURNS: int = Field(
        default=30,
        description="Number of recent voice turns to inject as immediate context"
    )

    # Reasoning in voice mode (configured independently from chat mode)
    VOICE_EXTENDED_THINKING: bool = Field(
        default=False,
        description=(
            "If false, the LLM does NOT reason in voice mode (minimum latency). "
            "If true, uses VOICE_THINKING_EFFORT. Chat mode is unaffected — it keeps EXTENDED_THINKING/THINKING_EFFORT."
        )
    )
    VOICE_THINKING_EFFORT: str = Field(
        default="low",
        description="Reasoning effort for voice only: low | medium | high | xhigh | max"
    )
    # Voice-only Claude model (independent from chat). Haiku is ~3-5x faster than Opus.
    VOICE_CLAUDE_MODEL: str = Field(
        default="claude-haiku-4-5",
        description="Claude model for voice only. Haiku = faster. Empty = use ANTHROPIC_MODEL."
    )
    # How much long-term memory to load in voice mode (in chars). 0 = none (much faster).
    VOICE_MEMORY_MAX_CHARS: int = Field(
        default=0,
        description="Chars of saved-memory .md to inject in voice. 0 = none (fast), -1 = use MEMORY_MAX_CHARS"
    )

    # ElevenLabs voice_settings (affect how the voice sounds)
    VOICE_SPEED: float = Field(
        default=0.92,
        description="Speaking rate. 1.0 = normal, <1 = slower, >1 = faster. 0.88–0.95 sounds natural in conversation."
    )
    VOICE_STABILITY: float = Field(
        default=0.5,
        description="0-1. Higher = more consistent and predictable; lower = more expressive but variable."
    )
    VOICE_SIMILARITY_BOOST: float = Field(
        default=0.8,
        description="0-1. How closely the output matches the source voice's timbre."
    )
    VOICE_STYLE: float = Field(
        default=0.15,
        description="0-1. Stylistic exaggeration; a touch of style helps it sound natural rather than robotic."
    )
    VOICE_USE_SPEAKER_BOOST: bool = Field(
        default=True,
        description="Amplify similarity to the original speaker."
    )


# Global config instance
settings = Config()
