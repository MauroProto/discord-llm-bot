"""Backward-compatibility shim.

Historically the bot only supported Anthropic Claude and the codebase imported
`claude_client` everywhere. The provider abstraction (PR1) keeps that public
surface stable while the actual implementation moved to `providers/`.

Importing `claude_client` returns whichever provider is configured via
`LLM_PROVIDER` (default: anthropic). The exposed methods are
`generate_response`, `generate_voice_response`, and `analyze_conversation`.

When new providers land (OpenAI, Gemini, OpenRouter, ...) callers can already
benefit by changing one env var — no code changes here.
"""

from __future__ import annotations

from providers import get_provider
from providers.anthropic_provider import LAIN_PERSONALITY  # re-exported for compat

# Resolve the configured provider once. `get_provider` is itself cached so
# subsequent calls anywhere reuse the same instance.
claude_client = get_provider()

__all__ = ["claude_client", "LAIN_PERSONALITY"]
