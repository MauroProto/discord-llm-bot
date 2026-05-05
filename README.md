<div align="center">

# discord-llm-bot

A self-hosted Discord bot that talks via **Claude, GPT, Gemini, or OpenRouter** — your choice — with optional **voice channel support** (ElevenLabs TTS + Scribe STT) and a **single unified memory store** across text and voice.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7-5865F2.svg?logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Deploy on Railway](https://img.shields.io/badge/Railway-deployable-0B0D0E.svg?logo=railway&logoColor=white)](https://railway.app/)

</div>

---

## What you get

- **One bot, four providers.** Switch between Anthropic Claude, OpenAI GPT/o-series, Google Gemini, and OpenRouter (a single key for 100+ models) by changing one env var.
- **Pluggable personalities.** Three built-in presets (`friendly`, `snarky`, `analyst`) and a path to drop in your own — either as a markdown file or as a literal env-var override.
- **Voice channels.** `!join` and the bot enters a voice channel, transcribes everyone with ElevenLabs Scribe, replies through the speaker with ElevenLabs TTS, and remembers it all.
- **Unified text + voice memory.** Voice transcripts and text messages live in the same daily `.md`. The bot recalls in chat what was said in voice, and the other way around.
- **Inline chat from voice.** While speaking on the call, the bot can drop a link, code snippet, or list into the text channel using `[CHAT: …]` / `[SOLO_CHAT: …]` markers — no special command needed.
- **Production patches included.** DAVE encryption monkey-patch (Discord's 2026 E2EE protocol), anti-echo, cancel-on-new-input, prompt caching for fast follow-up turns. The unsexy stuff that actually makes voice work.

## Quick start

### 1. Create a Discord bot

1. New application: <https://discord.com/developers/applications>.
2. Add a **Bot**, copy the token.
3. Enable the **Message Content**, **Server Members**, and **Voice State** intents.
4. Generate an OAuth2 invite URL with these permissions:
   - `Send Messages`, `Read Message History`, `View Channels`, `Embed Links`
   - `Connect`, `Speak`, `Use Voice Activity` *(only if you want voice)*
5. Invite the bot to your server.

### 2. Pick a provider and get an API key

| Provider | Key page | Best when |
|---|---|---|
| **Anthropic** | <https://console.anthropic.com/> | You want Claude (default; great quality, native web search/fetch, prompt caching, 1M context) |
| **OpenAI** | <https://platform.openai.com/api-keys> | You want GPT-5.4, o3, o4-mini, GPT-4.1 (1M context) |
| **Google** | <https://aistudio.google.com/apikey> | You want Gemini 2.5/3.x (1M context, fastest Flash variants) |
| **OpenRouter** | <https://openrouter.ai/keys> | You want a single key that works across all providers (passthrough pricing) |

**Optional** for voice: <https://elevenlabs.io/app/settings/api-keys>.

### 3. Configure

```bash
git clone https://github.com/MauroProto/discord-llm-bot.git discord-llm-bot
cd discord-llm-bot
cp .env.example .env
# Open .env and fill in DISCORD_BOT_TOKEN, ALLOWED_GUILD_ID,
# LLM_PROVIDER, and the matching <PROVIDER>_API_KEY.
```

### 4. Run

#### Local (macOS / Linux)

```bash
brew install ffmpeg opus libsodium      # macOS
# sudo apt install ffmpeg libopus0      # Debian / Ubuntu

pip install --pre -r requirements.txt   # --pre is required (voice-recv ships only alphas)
python3 bot.py
```

#### Docker

```bash
docker compose up --build
```

#### Railway

1. Connect this repo on <https://railway.app/>.
2. The `Dockerfile` and `railway.json` are picked up automatically.
3. Paste your environment variables into the **Variables** tab.
4. Deploy. The bot starts immediately and reconnects on restart.

## Switching providers

Set `LLM_PROVIDER` to one of `anthropic | openai | gemini | openrouter` and provide the matching key. Examples:

```bash
# Anthropic Claude (default)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-7

# OpenAI GPT-5
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.4

# Google Gemini
LLM_PROVIDER=gemini
GOOGLE_API_KEY=...
GEMINI_MODEL=gemini-2.5-pro

# OpenRouter (one key, many models)
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-opus-4-7
# Append :online to the model id to enable web search:
# OPENROUTER_MODEL=openai/gpt-5.4:online
```

You can also point a **different** model at voice replies — e.g. flagship in chat, mini/flash in voice — via `VOICE_OPENAI_MODEL`, `VOICE_GEMINI_MODEL`, `VOICE_OPENROUTER_MODEL`, or `VOICE_CLAUDE_MODEL`.

## Pick a personality

Three presets ship with the repo:

```bash
BOT_PERSONALITY=friendly   # default — warm, helpful, conversational
BOT_PERSONALITY=snarky     # witty, dry, no-filter (still kind)
BOT_PERSONALITY=analyst    # precise, structured, data over opinion
```

Add your own in `personalities/`. See [`personalities/README.md`](personalities/README.md) for the format. To override entirely with a custom prompt:

```bash
CUSTOM_SYSTEM_PROMPT="You are HAL, a slightly menacing assistant…"
# or
CUSTOM_SYSTEM_PROMPT_FILE=/etc/secrets/my-personality.md
```

`{{BOT_NAME}}` in the prompt is replaced with `BOT_DISPLAY_NAME` (or your bot's actual Discord username).

## Commands

| Command | Aliases | What it does |
|---|---|---|
| `@bot <message>` | — | Reply with full chat context + long-term memory |
| `!summary [N]` | `!resumen` | Summarise the last *N* messages (default 50) |
| `!context` | `!contexto` | Send today's saved `.md` context file |
| `!search <query>` | `!buscar` | Manual web search via the configured fallback |
| `!join` | — | Join the voice channel (uses `VOICE_CHANNEL_ID` if set) |
| `!leave` | — | Leave the voice channel |
| `!say <text>` | `!sayvoz` | Force a TTS line in the current VC |
| `!info` | `!lain` | Bot info (provider, model, personality) |
| `!help` | `!helpbot` | List commands |

You can also trigger voice with natural language: `@bot join voice` or `@bot leave the call`.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Discord (text + voice)                       │
└──────────────┬──────────────────────────────────┬───────────────────┘
               │ messages                         │ RTP / DAVE
               ▼                                  ▼
       ┌────────────────┐               ┌────────────────────┐
       │     bot.py     │               │  voice_manager.py  │
       │  (commands +   │               │  (sessions, VAD,   │
       │   on_message)  │               │   anti-echo, DAVE  │
       └────────┬───────┘               │   patch, cancel-   │
                │                       │   on-new-input)    │
                │                       └─────────┬──────────┘
                │                                 │ WAV (16 k mono)
                │                                 ▼
                │                       ┌────────────────────┐
                │                       │ elevenlabs_client  │
                │                       │ Scribe STT (HTTP)  │
                │                       └─────────┬──────────┘
                │                                 │ transcript
                ▼                                 ▼
       ┌──────────────────────────────────────────────────┐
       │              context_manager.py                   │
       │  Channel history + daily .md ← unified store →   │
       │  load_recent_memory(days, max_chars)              │
       └────────────────────────┬─────────────────────────┘
                                │ messages + memory
                                ▼
                  ┌────────────────────────────────┐
                  │      providers/get_provider()  │
                  │  Anthropic | OpenAI | Gemini   │
                  │           | OpenRouter         │
                  └─────────────┬──────────────────┘
                                │ reply text
                ┌───────────────┴───────────────┐
                ▼                               ▼
       ┌─────────────────┐            ┌──────────────────┐
       │  Discord text   │            │  ElevenLabs TTS  │
       │  reply / chat   │            │  (turbo / v3)    │
       └─────────────────┘            └────────┬─────────┘
                                               │ MP3 / Opus
                                               ▼
                                      ┌──────────────────┐
                                      │  ffmpeg + Discord │
                                      │   voice playback  │
                                      └──────────────────┘
```

### Voice pipeline notes

- **DAVE patch.** Discord migrated to a new end-to-end encryption protocol in 2026. The upstream `discord-ext-voice-recv 0.5.2a179` doesn't decrypt DAVE, so we monkey-patch `PacketDecoder` at runtime to call `dave_session.decrypt()` before Opus decoding. Code is inlined from upstream [PR #54](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54).
- **HTTP STT.** The async ElevenLabs SDK hangs on long audio without raising. We use `aiohttp` directly with an explicit 20-second timeout.
- **Anti-echo.** When the bot is speaking, incoming audio is ignored (`_is_speaking` flag plus 800 ms guard) so it doesn't barge-in on its own voice.
- **Cancel on new input.** Only the most recent user turn is answered. Pending LLM/TTS tasks get cancelled, but STT runs in an independent task so it never gets killed mid-request.
- **Prompt caching.** Long-term memory becomes a cached prefix, so the second voice reply onward only reprocesses the delta. Anthropic uses explicit `cache_control: ephemeral`; OpenAI/Gemini cache automatically on stable prefixes.

## Configuration

See [`.env.example`](.env.example) for the full list with comments. The most useful knobs:

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `gemini` \| `openrouter` |
| `BOT_PERSONALITY` | `friendly` | One of `friendly` \| `snarky` \| `analyst`, or any file in `personalities/` |
| `BOT_DISPLAY_NAME` | live username | Substituted for `{{BOT_NAME}}` in the prompt |
| `EXTENDED_THINKING` | `true` | Reasoning in chat mode |
| `VOICE_EXTENDED_THINKING` | `false` | Off in voice mode for low latency |
| `VOICE_SILENCE_MS` | `1000` | Silence (ms) that closes a turn |
| `VOICE_IDLE_TIMEOUT_SECONDS` | `120` | Auto-leave the VC if nobody speaks |
| `VOICE_MEMORY_MAX_CHARS` | `-1` | `-1` = full memory; `0` = none (faster) |
| `MEMORY_DAYS` | `14` | How many days of saved context to inject as long-term memory |
| `LEGACY_SELF_PREFIXES` | — | Optional CSV of historical bot names if you renamed the bot |

## Storage

Daily context lives in `./data/contexts/YYYY-MM-DD.md` (one file per day, both text and voice in the same file with `#VOICE:<channel>` tags for voice exchanges). Threads get their own files in `./data/threads/`.

For Railway, mount a persistent volume on `/app/data`. For local Docker, the included `docker-compose.yml` already does this.

## Why not subscriptions?

> **Can I plug this into my Claude Pro / ChatGPT Plus / Gemini Advanced subscription instead of paying API?**
>
> **Not legitimately.** Per provider:
>
> - **Anthropic.** OAuth tokens (`sk-ant-oat01-…`) exist for Claude Code personal use. Anthropic *prohibits* programmatic use, and as of January 2026 third-party OAuth was shut down. Detected → ban. Use API keys (`sk-ant-api03-…`).
> - **OpenAI.** OAuth exists for the Codex CLI. OpenAI's docs explicitly recommend API keys for any app that calls the API. Subscription OAuth is scoped to Codex CLI, not general programmatic use.
> - **Google.** AI Studio API keys are separate from your Gemini Advanced subscription.
>
> Real alternatives if you want to avoid paying multiple APIs:
>
> 1. **OpenRouter** — one key for 100+ models, passthrough pricing. **This is the closest thing** to "use my one subscription everywhere".
> 2. **Ollama** (local) — free, open-weight models (Llama 3, Qwen, DeepSeek). Requires GPU/CPU. Provider support is on the roadmap.
> 3. **Pay-as-you-go API.** For low-traffic Discord servers, this often costs less than a Pro subscription.
>
> We won't ship OAuth-subscription bridges because they violate ToS, get accounts banned, and break without notice.

## Project files

| File | Purpose |
|---|---|
| `bot.py` | Discord event handlers, command definitions, entrypoint |
| `config.py` | Pydantic settings loader |
| `providers/` | LLM provider abstraction — Anthropic, OpenAI, Gemini, OpenRouter |
| `personalities/` | Personality presets and loader |
| `context_manager.py` | Reads channel history, persists daily `.md`, loads long-term memory |
| `elevenlabs_client.py` | HTTP client for ElevenLabs TTS and Scribe STT |
| `voice_manager.py` | Voice sessions, DAVE monkey-patch, VAD, anti-echo, cancel logic |
| `search_client.py` | Tavily / SerpAPI / DuckDuckGo fallback search |
| `Dockerfile` | Python 3.11 + ffmpeg + libopus production image |
| `docker-compose.yml` | Local dev with a persistent `./data` volume |
| `railway.json` | Railway deploy descriptor |
| `requirements.txt` | Python dependencies (install with `--pre`) |
| `SECURITY.md` | Security policy |
| `CONTRIBUTING.md` | Developer guide |
| `LICENSE` | MIT |

## Roadmap

- [ ] Streaming TTS (start playback while the LLM is still generating)
- [ ] Streaming STT (Deepgram / AssemblyAI) for sub-2 s latency
- [ ] Smart barge-in (interrupt the bot only with sustained voice activity)
- [ ] Per-user voice profiles (different ElevenLabs voices for different roles)
- [ ] Slash commands `/join`, `/leave`, etc. instead of prefix commands
- [ ] Ollama provider for self-hosted local models

## Security

See [SECURITY.md](SECURITY.md) for the threat model and how to report vulnerabilities. Short version:

- **Never commit `.env`** — it's in `.gitignore` for a reason.
- **Use a platform secrets manager** in production (Railway Variables, GitHub Secrets, etc.).
- **Set `ALLOWED_GUILD_ID`** so the bot can't be invited to servers you don't control.

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the conventions and the things that need extra care (DAVE patch, anti-echo, cancel logic, prompt caching).

## License

[MIT](LICENSE) — do whatever you want, just keep the copyright notice.

## Acknowledgements

- [discord.py](https://github.com/Rapptz/discord.py) and [discord-ext-voice-recv](https://github.com/imayhaveborkedit/discord-ext-voice-recv) for everything voice-related.
- [Anthropic](https://anthropic.com/), [OpenAI](https://openai.com/), [Google](https://ai.google.dev/), and [OpenRouter](https://openrouter.ai/) for the model APIs.
- [ElevenLabs](https://elevenlabs.io/) for TTS and Scribe.
- [@rdphillips7](https://github.com/rdphillips7)'s [PR #54](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54) — the DAVE decryption logic that voice receive needed in 2026 (replicated inline as a runtime monkey-patch).
