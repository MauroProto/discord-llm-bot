# Contributing

Thanks for thinking about contributing! This project is small but production-grade — we keep it simple, predictable, and easy to deploy.

## Quick start

```bash
git clone https://github.com/MauroProto/discord-claude-bot.git
cd discord-claude-bot
cp .env.example .env          # fill in your API keys
pip install --pre -r requirements.txt
brew install ffmpeg opus      # macOS — Linux: apt install ffmpeg libopus0
python3 bot.py
```

`--pre` is required because `discord-ext-voice-recv` only ships pre-releases.

## Project layout

```
bot.py              # Discord event handlers + slash commands + entrypoint
config.py           # Pydantic settings loaded from .env
claude_client.py    # Anthropic SDK wrapper with prompt caching
context_manager.py  # Reads channel history + persists daily .md files
elevenlabs_client.py# HTTP client for ElevenLabs TTS + STT (Scribe)
voice_manager.py    # Voice sessions, DAVE patch, VAD, anti-echo, cancel logic
search_client.py    # Optional Tavily / SerpAPI fallback search
Dockerfile          # Production image (Python 3.11 + ffmpeg + libopus)
docker-compose.yml  # Local dev with persistent ./data volume
railway.json        # Railway deploy config
```

## Coding conventions

- **Python 3.11+** with type hints where they help readability.
- Async-first. Long-running blocking calls go through `aiohttp` / `asyncio.create_task`.
- Defensive try/except in any hot path that can be killed by a transient error (Discord voice, Opus, ElevenLabs).
- Logs use plain `print` with bracketed tags so they're greppable: `[VOZ]`, `[OPUS]`, `[DAVE]`, `[VOZ][STT]`, `[VOZ][TTS]`. Don't introduce new tags without a reason.
- No new top-level files unless they pull their weight. Prefer extending an existing module.

## Testing changes

Before pushing:

```bash
# Syntax / import check
python3 -c "import bot, voice_manager, claude_client, elevenlabs_client; print('ok')"

# Smoke test parser of inline chat marks
python3 -c "
from voice_manager import _split_voice_and_chat
print(_split_voice_and_chat('Te paso por chat. [CHAT: https://x.com]'))
print(_split_voice_and_chat('[SOLO_CHAT: lista]'))
"
```

For voice changes, the only real test is running the bot, joining a voice channel, and checking the runtime logs:

```bash
railway logs                        # Railway runtime
docker compose logs -f discord-bot  # local
```

## Pull requests

- Branch off `main`, name your branch descriptively (`fix/dave-decoder-cascade`, `feat/streaming-tts`).
- Keep PRs scoped — one logical change per PR.
- In the description, explain **why** the change is needed (link to the issue/log/symptom that motivated it). The diff already shows the **what**.
- For voice-related changes, paste a snippet of the runtime logs that proves the change works.
- Don't touch dependency pins unless you're explicitly upgrading.

## Things that need extra care

- **DAVE encryption monkey-patch** in `voice_manager._patch_voice_recv_for_dave()` — replicates an unmerged upstream PR. If you change this, document why and verify with logs that `[DAVE] decrypt ok` keeps appearing.
- **Anti-echo logic** (`_is_speaking`, `ECHO_GUARD_MS`, VAD threshold) — lower thresholds = more false barge-ins; higher = misses real interrupts. Test both extremes.
- **Cancel-on-new-input** — the STT must run in a task that is *not* cancellable from `_silence_timer`. Don't change this without tracing the failure mode in the [bug history](#).
- **Prompt caching** — `system` becomes a list with `cache_control` only when memory is non-empty. Don't break this contract; the latency gain is significant.

## Releasing

There are no formal releases — `main` is what runs in production. Prefer small, reversible commits over big bangs.
