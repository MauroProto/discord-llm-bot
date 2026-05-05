# Personalities

A personality is a `.md` file that defines how the bot talks. The system prompt sent to the LLM is the **Identity** + **Communication style** sections, with `{{BOT_NAME}}` substituted for whatever you set in `BOT_DISPLAY_NAME` (or the bot's actual Discord username).

## Built-in personalities

| Id | Style | Best for |
|---|---|---|
| `friendly` | Warm, conversational, helpful without performing helpfulness | Community servers, general use |
| `snarky` | Sharp, witty, unfiltered honesty (without being cruel) | Dev/friend servers, brainstorming |
| `analyst` | Precise, structured, data over opinion | Work servers, research, evaluation |

Pick one with the `BOT_PERSONALITY` env var:

```bash
BOT_PERSONALITY=snarky
```

## Adding your own personality

Drop a `.md` file in this directory matching the format below, and reference it in `BOT_PERSONALITY`.

```markdown
---
id: my-bot
name: My Bot
language: en
---

## Identity

You are {{BOT_NAME}}, a [description]…

## Communication style

- [How you talk]
- [What you do and don't do]

## Voice mode adjustments

When this response will be played through TTS:

- [How voice replies should differ]
```

### Frontmatter fields

- `id` — must match the filename (without `.md`). Used in `BOT_PERSONALITY`.
- `name` — display name (informational).
- `language` — ISO code. Informational; doesn't change behavior.

### Sections

- **Identity** — who the bot is.
- **Communication style** — how it talks. The bulk of the prompt.
- **Voice mode adjustments** — appended only when generating a TTS reply. The shared rules about `[CHAT: ...]` markers and audio tag handling are inserted automatically — you don't need to repeat them.

## Bringing your own personality without committing it

If you maintain a private fork or want a personality that only your deploy uses, set `CUSTOM_SYSTEM_PROMPT` in your environment with the full text inline:

```bash
CUSTOM_SYSTEM_PROMPT="You are HAL, a helpful but slightly menacing assistant…"
```

Or point at a file outside this directory:

```bash
CUSTOM_SYSTEM_PROMPT_FILE=/secrets/my-personality.md
```

Either of those overrides `BOT_PERSONALITY` entirely. Useful when your prompt contains business rules you don't want in the public repo.
