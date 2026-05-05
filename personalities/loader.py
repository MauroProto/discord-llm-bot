"""Personality loader.

Resolves the active system prompt from one of three sources, in this order:

1. `CUSTOM_SYSTEM_PROMPT` env var (literal string) — total override.
2. `CUSTOM_SYSTEM_PROMPT_FILE` env var (path) — read the file and use as-is.
3. `BOT_PERSONALITY` (default `friendly`) — load `personalities/{id}.md`.

The chosen personality is parsed for its YAML frontmatter and its body. The
body has two relevant sections:

- `## Identity` and `## Communication style` are concatenated to form the
  base system prompt sent to the LLM in chat mode.
- `## Voice mode adjustments` is appended only when the caller is building
  a voice (TTS) response. The bot's shared voice rules (CHAT/SOLO_CHAT
  markers, audio tag handling per TTS model) are added automatically by
  the provider — personality files don't need to repeat them.

`{{BOT_NAME}}` placeholders are replaced with the configured display name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from config import settings


_THIS_DIR = Path(__file__).resolve().parent

# A super-light frontmatter parser. We only need 3 fields and avoid pulling
# pyyaml as a dep just for that. Lines look like `key: value`.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)$",
    re.DOTALL,
)
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Personality:
    """A resolved personality ready to use."""
    id: str
    name: str
    chat_prompt: str       # Identity + Communication style, BOT_NAME-rendered
    voice_section: str     # `## Voice mode adjustments` body, BOT_NAME-rendered


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return ({key: value}, body). If no frontmatter, return ({}, full text)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    meta: dict[str, str] = {}
    for line in m.group("meta").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip("\"'")

    return meta, m.group("body")


def _split_sections(body: str) -> dict[str, str]:
    """Split a markdown body by `## Heading`. Returns {heading: content}.

    Heading text is matched case-insensitively and stripped. Content runs
    from after the heading line until the next `## ` or end of file.
    """
    headings = list(_HEADING_RE.finditer(body))
    if not headings:
        return {"": body.strip()}

    sections: dict[str, str] = {}
    for i, m in enumerate(headings):
        title = m.group(1).strip().lower()
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        sections[title] = body[start:end].strip()

    return sections


def _render(text: str, bot_name: str) -> str:
    """Substitute placeholders. Currently only `{{BOT_NAME}}`."""
    return text.replace("{{BOT_NAME}}", bot_name)


def _load_from_file(path: Path, bot_name: str, fallback_id: str) -> Personality:
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    sections = _split_sections(body)

    identity = sections.get("identity", "").strip()
    style = sections.get("communication style", "").strip()
    voice = sections.get("voice mode adjustments", "").strip()

    chat_pieces: list[str] = []
    if identity:
        chat_pieces.append(identity)
    if style:
        chat_pieces.append("## Communication style\n\n" + style)

    chat_prompt = "\n\n".join(chat_pieces) if chat_pieces else body.strip()

    return Personality(
        id=meta.get("id", fallback_id),
        name=meta.get("name", fallback_id.title()),
        chat_prompt=_render(chat_prompt, bot_name),
        voice_section=_render(voice, bot_name),
    )


def _from_inline_text(text: str, bot_name: str, fallback_id: str = "custom") -> Personality:
    """Build a Personality from a literal string. Used by CUSTOM_SYSTEM_PROMPT.

    No frontmatter parsing — we treat the whole string as the chat prompt.
    Voice mode falls back to empty (only the shared voice rules apply).
    """
    return Personality(
        id=fallback_id,
        name=fallback_id.title(),
        chat_prompt=_render(text.strip(), bot_name),
        voice_section="",
    )


def load_personality(personality_id: str | None = None, bot_name: str = "the bot") -> Personality:
    """Resolve the active personality based on env config.

    Args:
        personality_id: Override of `settings.BOT_PERSONALITY`. Useful in
            tests or for ad-hoc switching.
        bot_name: Substituted for `{{BOT_NAME}}` in the prompt.
    """
    # 1) Inline custom override.
    if settings.CUSTOM_SYSTEM_PROMPT:
        return _from_inline_text(settings.CUSTOM_SYSTEM_PROMPT, bot_name)

    # 2) File-based custom override.
    if settings.CUSTOM_SYSTEM_PROMPT_FILE:
        path = Path(settings.CUSTOM_SYSTEM_PROMPT_FILE).expanduser().resolve()
        if path.exists():
            return _load_from_file(path, bot_name, fallback_id="custom")
        # Fall through with a warning if the file is missing — better than
        # crashing the bot at startup.
        print(
            f"[personality] CUSTOM_SYSTEM_PROMPT_FILE={path} not found, "
            f"falling back to BOT_PERSONALITY"
        )

    # 3) Built-in personality from this directory.
    pid = (personality_id or settings.BOT_PERSONALITY or "friendly").lower().strip()
    candidate = _THIS_DIR / f"{pid}.md"
    if not candidate.exists():
        # Quietly fall back to friendly so a typo doesn't take the bot down.
        print(
            f"[personality] {pid!r} not found in personalities/, "
            f"falling back to 'friendly'"
        )
        candidate = _THIS_DIR / "friendly.md"

    return _load_from_file(candidate, bot_name, fallback_id=pid)


__all__ = ["Personality", "load_personality"]
