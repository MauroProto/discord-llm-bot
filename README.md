<div align="center">

# Lain — Discord bot with Claude + ElevenLabs voice

A Discord bot that **chats with Claude** and **talks in voice channels** with ElevenLabs.
It listens to the call, replies with TTS, mirrors info to text, and keeps a single unified context across both.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7-5865F2.svg?logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
[![Anthropic](https://img.shields.io/badge/Anthropic-Claude%204.x-D97757.svg)](https://anthropic.com/)
[![ElevenLabs](https://img.shields.io/badge/ElevenLabs-TTS%20%2B%20Scribe-000000.svg)](https://elevenlabs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Deploy on Railway](https://img.shields.io/badge/Railway-deployable-0B0D0E.svg?logo=railway&logoColor=white)](https://railway.app/)

</div>

---

## What it does

- **Text chat** — mention `@Lain` and she replies with full channel context, long-term memory of the last *N* days, and Claude Opus reasoning.
- **Voice chat** — `!join` and she enters a voice channel, transcribes everyone with ElevenLabs Scribe, replies through the speaker with ElevenLabs TTS, and remembers it all.
- **Unified memory** — voice transcripts and text messages live in the same daily `.md`. She remembers in chat what was said in voice, and vice versa.
- **Sends to chat from voice** — Claude can decide on its own to drop a link, code snippet or list into the text channel while it's still talking on the call (`[CHAT: ...]` / `[SOLO_CHAT: ...]` inline marks).
- **Production-tested workarounds** — DAVE (Discord's new E2EE protocol) decryption patch, anti-echo, cancel-on-new-input, and a Scribe HTTP client that doesn't hang on long audio.

## Demo

```
You (text):     @Lain qué hicimos ayer con el deploy?
Lain (text):    Subiste el fix de DAVE a Railway anoche, anduvo todo. ✅

You (text):     !join
Lain (text):    🎙️ Estoy en General. Hablen tranqui que escucho y participo.

You (voice):    "che pasame el link de la doc de Anthropic"
Lain (voice):   "Te lo paso por chat ahora"
Lain (text):    https://docs.anthropic.com

You (voice):    "y el código para listar archivos en Python?"
Lain (text):    ```python
                from pathlib import Path
                for f in Path(".").iterdir():
                    print(f.name)
                ```

You (text, later): @Lain qué me pediste hace un rato por voz?
Lain (text):    Me pediste el link de la doc de Anthropic y un snippet de Python.
```

## Quick start

### 1. Discord setup

1. Create an application at <https://discord.com/developers/applications>.
2. Add a **Bot**, copy its token.
3. Enable the **Message Content**, **Server Members** and **Voice State** intents.
4. Generate an OAuth2 invite URL with these permissions:
   - `Send Messages`, `Read Message History`, `View Channels`, `Embed Links`
   - `Connect`, `Speak`, `Use Voice Activity` (voice features)
5. Invite the bot to your server.

### 2. API keys

| Service | Where | Purpose |
|---|---|---|
| Anthropic | <https://console.anthropic.com/> | Claude (chat + voice) |
| ElevenLabs | <https://elevenlabs.io/app/settings/api-keys> | TTS + STT |
| Tavily *(optional)* | <https://tavily.com/> | Web search fallback |
| SerpAPI *(optional)* | <https://serpapi.com/> | Web search fallback |

### 3. Configure

```bash
git clone https://github.com/MauroProto/discord-claude-bot.git
cd discord-claude-bot
cp .env.example .env
# open .env and fill in your tokens / keys
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

#### Railway (one-click)

1. Connect this repo on <https://railway.app/>.
2. The `Dockerfile` and `railway.json` are picked up automatically.
3. Paste your environment variables in the **Variables** tab.
4. Deploy. The bot starts immediately and reconnects on restart.

## Commands

| Command | Aliases | Action |
|---|---|---|
| `@Lain <message>` | — | Reply with full chat context + memory |
| `!resumen [N]` | — | Summarise the last *N* messages (default 50) |
| `!contexto` | — | Send today's saved `.md` |
| `!buscar <query>` | — | Manual web search fallback |
| `!join` | `!entra`, `!vozon`, `!meteteacanal` | Join the configured voice channel |
| `!leave` | `!sali`, `!vozoff`, `!chau` | Leave the voice channel |
| `!sayvoz <text>` | — | Force a TTS line in the current VC |
| `!lain`, `!helpbot` | — | Bot info / help |

You can also use natural language: `@Lain metete al canal de voz` and `@Lain andate del canal` work too.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Discord (text + voice)                       │
└────────────────┬─────────────────────────────────┬──────────────────┘
                 │ messages                        │ RTP / DAVE
                 ▼                                 ▼
        ┌────────────────┐              ┌────────────────────┐
        │     bot.py     │              │  voice_manager.py  │
        │  (commands +   │              │  (per-guild        │
        │   on_message)  │              │   sessions, VAD,   │
        └────────┬───────┘              │   anti-echo, DAVE  │
                 │                      │   monkey-patch)    │
                 │                      └────────┬───────────┘
                 │                               │ WAV (16k mono)
                 │                               ▼
                 │                      ┌────────────────────┐
                 │                      │ elevenlabs_client  │
                 │                      │ Scribe STT (HTTP)  │
                 │                      └────────┬───────────┘
                 │                               │ transcript
                 ▼                               ▼
        ┌──────────────────────────────────────────────────┐
        │              context_manager.py                   │
        │  Channel history + daily .md ← unified store →   │
        │  load_recent_memory(days, max_chars)              │
        └────────────────────────┬─────────────────────────┘
                                 │ messages + memory
                                 ▼
                       ┌──────────────────────┐
                       │   claude_client.py   │
                       │  Opus 4.7 (chat) /   │
                       │  Haiku 4.5 (voice)   │
                       │  + prompt caching    │
                       └─────────┬────────────┘
                                 │ reply text
                  ┌──────────────┴───────────────┐
                  │                              │
                  ▼                              ▼
        ┌─────────────────┐           ┌──────────────────┐
        │ Discord text    │           │ ElevenLabs TTS    │
        │  reply / chat   │           │ (turbo / v3)      │
        └─────────────────┘           └────────┬──────────┘
                                               │ MP3 / Opus
                                               ▼
                                      ┌──────────────────┐
                                      │  ffmpeg + Discord │
                                      │   voice playback  │
                                      └──────────────────┘
```

### Voice pipeline highlights

- **DAVE patch** — Discord migrated to a new end-to-end encryption protocol in 2026. The upstream `discord-ext-voice-recv 0.5.2a179` doesn't decrypt DAVE, so we monkey-patch `PacketDecoder` at runtime to call `dave_session.decrypt()` before Opus decoding. Code is inlined from upstream [PR #54](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54).
- **HTTP STT** — the `elevenlabs` async SDK hangs on long audio without raising. We use `aiohttp` directly with an explicit 20-second timeout.
- **Anti-echo** — when the bot is speaking, incoming audio is ignored (`_is_speaking` flag plus 800 ms guard) so it doesn't barge-in on its own voice.
- **VAD** — per-packet RMS gate filters silence frames so the silence-timer can actually fire.
- **Cancel on new input** — only the most recent user turn is answered. Pending Claude/TTS gets cancelled, but the STT keeps running in an independent task so it never gets killed mid-request.
- **Prompt caching** — long-term memory becomes a cached system block, so the second voice reply onward only reprocesses the delta.

## Configuration

Everything is configured via `.env` (or your platform's secrets manager). See [`.env.example`](.env.example) for the full list with defaults and comments.

The most useful knobs:

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-opus-4-7` | Text chat model |
| `VOICE_CLAUDE_MODEL` | `claude-haiku-4-5` | Voice model — Haiku is 3-5× faster than Opus and good enough for short spoken replies |
| `EXTENDED_THINKING` | `true` | Adaptive thinking in chat |
| `VOICE_EXTENDED_THINKING` | `false` | Off in voice for low latency |
| `ELEVENLABS_TTS_MODEL` | `eleven_turbo_v2_5` | `eleven_v3` is more expressive (and supports inline audio tags), but ~2× slower |
| `VOICE_SILENCE_MS` | `1000` | Silence (ms) that closes a turn |
| `VOICE_MAX_TURN_SECONDS` | `15` | Force-flush long monologues |
| `VOICE_IDLE_TIMEOUT_SECONDS` | `120` | Auto-leave the VC if nobody speaks |
| `VOICE_MEMORY_MAX_CHARS` | `-1` | `-1` = use full long-term memory; `0` = none (faster) |
| `MEMORY_DAYS` | `14` | Days of saved context to inject as long-term memory |

## Storage

Daily context lives in `./data/contexts/YYYY-MM-DD.md` (one file per day, both text and voice in the same file with `#VOZ:<channel>` tags for voice exchanges). Threads get their own files in `./data/threads/`.

For Railway, mount a persistent volume on `/app/data`. For local Docker, the included `docker-compose.yml` already does this.

## Latency

A typical voice round-trip after the cache is warm:

```
silence detection   ~1.0 s
ElevenLabs Scribe   ~1.5 s
Claude Haiku (cached) ~1.0 s
ElevenLabs Turbo TTS ~0.7 s
network              ~0.3 s
─────────────────── ──────
total perceived     ~3.5–5 s
```

Streaming TTS (which would shave 1-2 seconds off the perceived latency) is on the roadmap.

## Project files

| File | Purpose |
|---|---|
| `bot.py` | Discord event handlers, command definitions, entrypoint |
| `config.py` | Pydantic settings loader |
| `claude_client.py` | Anthropic SDK wrapper with prompt caching and per-mode model overrides |
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

- [ ] Streaming TTS (start playback while Claude is still generating)
- [ ] Streaming STT (Deepgram/AssemblyAI) for sub-2 s latency
- [ ] Smart barge-in (interrupt the bot only with sustained voice activity)
- [ ] Per-user voice profiles (different ElevenLabs voices for different roles)
- [ ] Slash commands `/join`, `/leave`, etc. instead of prefix commands

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
- [Anthropic](https://anthropic.com/) for the Claude family of models.
- [ElevenLabs](https://elevenlabs.io/) for TTS and Scribe.
- [@rdphillips7](https://github.com/rdphillips7)'s [PR #54](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54) — the DAVE decryption logic that voice receive needed in 2026 (replicated inline as a runtime monkey-patch).

---

<div align="center">

Made for late-night hackathon calls — and for keeping the conversation going whether you're typing or talking.

</div>
