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


# Global config instance
settings = Config()
