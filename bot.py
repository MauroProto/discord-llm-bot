"""Discord LLM Bot — core module with event handlers and commands.

A multi-provider Discord bot powered by Claude / GPT / Gemini / OpenRouter,
with optional voice channel support via ElevenLabs (TTS + Scribe STT) and a
unified text+voice memory store. See README for setup.
"""

import base64
import os
import random
import re
from collections import deque
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

from config import settings
from context_manager import context_manager
from claude_client import claude_client
from search_client import search_client
from voice_manager import voice_manager, VOICE_RECV_AVAILABLE

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=settings.BOT_PREFIX,
    intents=intents,
    help_command=None,
)

# Track processed message IDs to avoid loops (bounded FIFO)
_PROCESSED_LIMIT = 1000
_processed_order: deque[int] = deque(maxlen=_PROCESSED_LIMIT)
_processed_messages: set[int] = set()


def _mark_processed(message_id: int) -> None:
    if len(_processed_order) == _PROCESSED_LIMIT:
        _processed_messages.discard(_processed_order[0])
    _processed_order.append(message_id)
    _processed_messages.add(message_id)


@bot.event
async def on_ready():
    """Bot is connected and ready."""
    print(f"[bot] connected as {bot.user} (ID: {bot.user.id})")
    print(f"[bot] allowed guild:   {settings.ALLOWED_GUILD_ID or 'any'}")
    print(f"[bot] allowed channel: {settings.ALLOWED_CHANNEL_ID or 'any'}")
    print(f"[bot] LLM provider:    {claude_client.name}")
    print(f"[bot] personality:     {getattr(claude_client._personality, 'id', 'n/a')}")
    # Let context_manager use the live Discord username when persisting
    # exchanges, so the saved markdown reflects the real bot identity.
    context_manager.set_bot_name(bot.user.name)

    # Sync slash commands. Guild-scoped sync is instant; global sync can take
    # up to an hour to propagate across Discord's CDN, but only needs to run
    # once per command set change.
    try:
        guild_obj = _allowed_guild_obj()
        if guild_obj:
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"[bot] synced {len(synced)} slash commands to guild {settings.ALLOWED_GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"[bot] synced {len(synced)} slash commands globally")
    except Exception as e:
        print(f"[bot] slash command sync failed: {e}")


def _should_respond(message: discord.Message) -> bool:
    """Determine if the bot should respond to a message.

    Returns True if:
    - The bot is mentioned (@bot)
    - Message starts with command prefix
    - Spontaneous mode is enabled and the keyword set matches
    """
    # Ignore own messages and empty messages
    if message.author.bot:
        return False
    if not message.content.strip():
        return False
    
    # Check if already processed (loop prevention)
    if message.id in _processed_messages:
        return False
    
    # Privacy: check allowed guild
    if settings.ALLOWED_GUILD_ID and message.guild:
        if message.guild.id != settings.ALLOWED_GUILD_ID:
            return False
    
    # Privacy: check allowed channel
    if settings.ALLOWED_CHANNEL_ID:
        if message.channel.id != settings.ALLOWED_CHANNEL_ID:
            return False
    
    # Always respond to @mentions
    if bot.user.mentioned_in(message):
        return True
    
    # Respond to commands
    if message.content.startswith(settings.BOT_PREFIX):
        return True
    
    # Spontaneous responses (opt-in, disabled by default)
    if settings.SPONTANEOUS_RESPONSE:
        hackathon_keywords = [
            "hackathon", "idea", "proyecto", "app", "feature",
            "mvp", "stack", "tecnologia", "pitch", "demo",
            "ia", "ai", "blockchain", "web", "mobile",
        ]
        content_lower = message.content.lower()
        if any(kw in content_lower for kw in hackathon_keywords):
            if random.random() < settings.SPONTANEOUS_PROBABILITY:
                return True
    
    return False


def _bot_self_prefixes() -> tuple[str, ...]:
    """Prefixes the bot may have used in its own past messages, so the
    history builder can strip them when re-feeding the conversation back
    to the LLM. Computed lazily because `bot.user` is only available
    after login. Includes any extra legacy prefixes from
    `LEGACY_SELF_PREFIXES` env (CSV) for forks that renamed."""
    legacy = [
        s.strip() for s in (settings.LEGACY_SELF_PREFIXES or "").split(",") if s.strip()
    ]
    if not bot.user:
        return tuple(legacy)
    name = bot.user.name
    discriminator = getattr(bot.user, "discriminator", None)
    runtime = [f"{name}:"]
    if discriminator and discriminator != "0":
        runtime.append(f"{name}#{discriminator}:")
    return tuple(runtime + legacy)


def _build_claude_history(history: list[dict], exclude_id: int | None = None) -> list[dict]:
    """Convert Discord history to Claude message format (without current message)."""
    messages = []
    for msg in history:
        if exclude_id is not None and msg.get("id") == exclude_id:
            continue
        if msg.get("is_bot"):
            content = msg["content"]
            for prefix in _bot_self_prefixes():
                if content.startswith(prefix):
                    content = content[len(prefix):].lstrip()
                    break
            messages.append({"role": "assistant", "content": content})
        else:
            content = f"{msg['author']}: {msg['content']}"
            messages.append({"role": "user", "content": content})
    return messages


_TEXT_EXTS = (
    ".md", ".txt", ".json", ".csv", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".html", ".css", ".yaml", ".yml", ".sh", ".log", ".sql", ".toml",
    ".xml", ".env", ".ini", ".cfg",
)
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_MAX_TEXT_BYTES = 200_000  # cap per text attachment


async def _build_user_content(message: discord.Message, bot_user_id: int):
    """Build the current user message content for Claude — text or multimodal blocks."""
    bot_handle = f"@{bot.user.name}" if bot.user else "@bot"
    cleaned_text = message.content.replace(f"<@{bot_user_id}>", bot_handle).strip()
    base = f"{message.author}: {cleaned_text}" if cleaned_text else f"{message.author}:"

    # Collect attachments from current message + referenced (replied-to) message
    attachments: list[discord.Attachment] = list(message.attachments)
    ref_text = ""
    if message.reference and message.reference.message_id:
        try:
            ref_msg = message.reference.resolved
            if ref_msg is None:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
            if ref_msg:
                attachments.extend(ref_msg.attachments)
                if ref_msg.content:
                    ref_text = (
                        f"[Replying to {ref_msg.author}: \"{ref_msg.content[:300]}\"]"
                    )
        except Exception as e:
            print(f"[REF] could not read referenced message: {e}")

    if ref_text:
        base = f"{ref_text}\n{base}"

    if not attachments:
        return base

    text_chunks: list[str] = []
    media_blocks: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for att in attachments:
            ct = (att.content_type or "").split(";")[0].strip().lower()
            fn_lower = att.filename.lower()

            try:
                async with session.get(
                    att.url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.read()
            except Exception as e:
                print(f"[ATTACH] error fetching {att.filename}: {e}")
                continue

            is_image = ct.startswith("image/") or fn_lower.endswith(_IMG_EXTS)
            is_pdf = ct == "application/pdf" or fn_lower.endswith(".pdf")
            is_text = ct.startswith("text/") or fn_lower.endswith(_TEXT_EXTS)

            if is_image:
                media_type = ct if ct.startswith("image/") else "image/png"
                media_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                })
                print(f"[ATTACH] image: {att.filename}")
            elif is_pdf:
                media_blocks.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                })
                print(f"[ATTACH] pdf: {att.filename}")
            elif is_text:
                content = data[:_MAX_TEXT_BYTES].decode("utf-8", errors="replace")
                truncated_note = " (truncado)" if len(data) > _MAX_TEXT_BYTES else ""
                text_chunks.append(
                    f"--- Adjunto: {att.filename}{truncated_note} ---\n{content}\n--- fin {att.filename} ---"
                )
                print(f"[ATTACH] text: {att.filename} ({len(data)} bytes)")
            else:
                # Try as text as last resort
                try:
                    content = data[:_MAX_TEXT_BYTES].decode("utf-8")
                    text_chunks.append(
                        f"--- Adjunto: {att.filename} ---\n{content}\n--- fin ---"
                    )
                    print(f"[ATTACH] unknown-as-text: {att.filename}")
                except UnicodeDecodeError:
                    print(f"[ATTACH] skipped binary: {att.filename}")

    full_text = base
    if text_chunks:
        full_text = base + "\n\n" + "\n\n".join(text_chunks)

    if media_blocks:
        return [*media_blocks, {"type": "text", "text": full_text}]
    return full_text


async def _stream_to_discord(
    message: discord.Message,
    claude_messages: list[dict],
    memory_text: str,
) -> str:
    """Stream the LLM response into Discord by editing the reply in place.

    Strategy:
    - Open the reply with a placeholder so the user sees activity immediately.
    - Accumulate streamed deltas in `buffer`.
    - Edit the message every `STREAM_EDIT_INTERVAL_MS` (rate-limit safety) and
      when at least `STREAM_EDIT_MIN_DELTA_CHARS` of new text are pending.
    - When the buffer crosses 1900 chars (Discord's 2000 cap minus margin),
      "lock" the current message and continue in a follow-up.

    Returns the full concatenated response text (for storage and voice mirror).
    """
    interval_s = max(0.1, settings.STREAM_EDIT_INTERVAL_MS / 1000)
    min_delta = max(1, settings.STREAM_EDIT_MIN_DELTA_CHARS)
    char_cap = 1900  # Discord hard cap is 2000; leave a margin for emoji escaping

    full: list[str] = []
    current_buffer: list[str] = []
    current_msg: discord.Message | None = None
    last_edit_at = 0.0
    last_edited_text = ""

    import time as _t

    async def _open_message(initial_text: str) -> discord.Message:
        """Open the very first reply with whatever we've accumulated."""
        text = initial_text or "…"
        return await message.reply(text[:char_cap], mention_author=False)

    async def _flush(force: bool = False) -> None:
        """Edit `current_msg` to reflect `current_buffer`, respecting rate limits."""
        nonlocal current_msg, last_edit_at, last_edited_text
        text = "".join(current_buffer)
        if not text:
            return

        # First message: create it
        if current_msg is None:
            current_msg = await _open_message(text)
            last_edit_at = _t.monotonic()
            last_edited_text = text
            return

        # Rate-limit guard
        delta = text[len(last_edited_text):] if text.startswith(last_edited_text) else ""
        if not force:
            if (_t.monotonic() - last_edit_at) < interval_s:
                return
            if len(delta) < min_delta:
                return

        try:
            await current_msg.edit(content=text[:char_cap])
            last_edit_at = _t.monotonic()
            last_edited_text = text
        except discord.HTTPException as e:
            print(f"[stream] edit failed (will retry): {e}")

    try:
        async for chunk in claude_client.stream_response(
            messages=claude_messages,
            memory_text=memory_text,
        ):
            if not chunk:
                continue
            full.append(chunk)
            current_buffer.append(chunk)

            # If the current segment overflowed, lock it and start a follow-up
            if sum(len(p) for p in current_buffer) > char_cap:
                await _flush(force=True)
                current_buffer = []
                last_edited_text = ""
                current_msg = await message.channel.send("…")
                last_edit_at = _t.monotonic()
                continue

            await _flush(force=False)

        # Final flush so the last delta is visible
        await _flush(force=True)

    except Exception as e:
        print(f"[stream] error: {e}")
        # Try to surface the failure inline rather than leaving a dangling "…"
        try:
            if current_msg is None:
                await message.reply(
                    f"Something broke while generating: {type(e).__name__}",
                    mention_author=False,
                )
            else:
                await current_msg.edit(
                    content=("".join(current_buffer) or "")
                    + f"\n\n[stream error: {type(e).__name__}]"
                )
        except Exception:
            pass

    return "".join(full).strip()


@bot.event
async def on_message(message: discord.Message):
    """Main message handler — detects mentions and triggers responses."""
    # DEBUG: log every received message
    print(
        f"[MSG] guild={getattr(message.guild, 'id', None)} "
        f"channel={message.channel.id} "
        f"author={message.author} "
        f"is_bot={message.author.bot} "
        f"mentioned_me={bot.user.mentioned_in(message) if bot.user else False} "
        f"content={message.content!r}"
    )

    # Process commands first
    await bot.process_commands(message)

    # Skip if we shouldn't respond
    if not _should_respond(message):
        print(f"[MSG] no respondo (filtros): canal_permitido={settings.ALLOWED_CHANNEL_ID}")
        return

    # Mark as processed
    _mark_processed(message.id)

    # Natural-language voice triggers: "join voice", "leave voice", etc.
    if bot.user and bot.user.mentioned_in(message):
        content = message.content
        if _LEAVE_NL_RE.search(content):
            ctx = await bot.get_context(message)
            await _do_leave(ctx)
            return
        if _JOIN_NL_RE.search(content):
            ctx = await bot.get_context(message)
            await _do_join(ctx)
            return

    try:
        async with message.channel.typing():
            # Fetch channel history (excluding the current message to avoid duplication)
            history = await context_manager.get_channel_history(message.channel)

            # Build claude messages: history + current message (with attachments if any)
            claude_messages = _build_claude_history(history, exclude_id=message.id)
            user_content = await _build_user_content(message, bot.user.id)
            claude_messages.append({"role": "user", "content": user_content})

            # Long-term memory: load recent .md context as background
            memory_text = context_manager.load_recent_memory(
                days=settings.MEMORY_DAYS,
                max_chars=settings.MEMORY_MAX_CHARS,
            )
            print(f"[MEM] loaded memory: {len(memory_text)} chars")

            # Generate response. Streaming = edit a single Discord message
            # in place as tokens arrive (low perceived latency). Falls back
            # to a single send for very long replies (>1900 chars per
            # message — Discord's hard cap is 2000).
            channel_name = getattr(message.channel, "name", None)

            if settings.STREAMING_REPLIES:
                response = await _stream_to_discord(
                    message=message,
                    claude_messages=claude_messages,
                    memory_text=memory_text,
                )
            else:
                response = await claude_client.generate_response(
                    messages=claude_messages,
                    memory_text=memory_text,
                )
                if response and len(response) <= 2000:
                    await message.reply(response, mention_author=False)
                elif response:
                    chunks = [response[i:i + 1900] for i in range(0, len(response), 1900)]
                    for i, chunk in enumerate(chunks):
                        if i == 0:
                            await message.reply(chunk, mention_author=False)
                        else:
                            await message.channel.send(chunk)

            if not response:
                return

            # Persist the exchange to the daily .md (and the thread file if applicable)
            context_manager.save_daily_context(
                channel_id=message.channel.id,
                bot_response=response,
                query=message.content,
                author=str(message.author),
                channel_name=channel_name,
            )
            if isinstance(message.channel, discord.Thread):
                context_manager.save_thread_context(
                    thread_id=message.channel.id,
                    bot_response=response,
                    query=message.content,
                    author=str(message.author),
                    thread_name=channel_name,
                )

            # Mirror to voice if the bot is currently connected to a VC in this guild
            if (
                settings.VOICE_MIRROR_TEXT
                and message.guild
                and voice_manager.is_connected(message.guild.id)
            ):
                session = voice_manager.get(message.guild.id)
                if session:
                    # Trunca para TTS
                    spoken = response
                    if len(spoken) > settings.VOICE_MAX_RESPONSE_CHARS:
                        spoken = spoken[:settings.VOICE_MAX_RESPONSE_CHARS].rsplit(".", 1)[0] + "."
                    try:
                        await session.speak(spoken)
                    except Exception as e:
                        print(f"[VOICE] mirror error: {e}")
    
    except Exception as e:
        print(f"Error en on_message: {e}")
        try:
            await message.reply(
                "Che, se me rompio algo interno. Reintentame en un toque.",
                mention_author=False
            )
        except Exception:
            pass


# ─── Commands ───

@bot.command(name="ask")
async def ask_cmd(ctx: commands.Context, *, question: str):
    """Ask the bot anything with full context."""
    handle = f"@{bot.user.name}" if bot.user else "@bot"
    await ctx.reply(
        f"You can mention me directly with {handle} and I'll reply with full "
        f"channel context. `!ask` also works as an explicit fallback.",
        mention_author=False,
    )


@bot.command(name="summary", aliases=["resumen"])
async def summary_cmd(ctx: commands.Context, limit: int = 50):
    """Summarise the last N messages of the current channel."""
    try:
        async with ctx.typing():
            history = await context_manager.get_channel_history(ctx.channel, limit=limit)
            chat_text = "\n".join([
                f"{m['author']}: {m['content']}"
                for m in history if m['content'].strip()
            ])

            response = await claude_client.analyze_conversation(
                history_text=chat_text,
                task=(
                    f"Summarise the following Discord conversation. Match the tone "
                    f"and language of the channel (don't translate). Cover the last "
                    f"{limit} messages: who said what, what was decided, what's open."
                ),
            )

            if len(response) <= 2000:
                await ctx.reply(f"**Summary:**\n{response}", mention_author=False)
            else:
                await ctx.reply(response[:1900] + "...", mention_author=False)

    except Exception as e:
        print(f"[bot] !summary error: {e}")
        await ctx.reply("Couldn't build the summary, try again.", mention_author=False)


@bot.command(name="context", aliases=["contexto"])
async def context_cmd(ctx: commands.Context):
    """Send today's saved context file."""
    try:
        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = context_manager.contexts_dir / f"{date_str}.md"

        if not filepath.exists():
            await ctx.reply(
                "No context saved yet today. Send me a message first.",
                mention_author=False,
            )
            return

        # Send as file if it's long
        file_size = filepath.stat().st_size
        if file_size > 8_000_000:  # 8MB Discord upload cap
            await ctx.reply(
                "Today's context is too big to upload in one piece.",
                mention_author=False,
            )
            return

        await ctx.reply(
            f"Here's today's saved context ({date_str}):",
            file=discord.File(filepath),
            mention_author=False,
        )

    except Exception as e:
        print(f"[bot] !context error: {e}")
        await ctx.reply("Couldn't read the context file, try again.", mention_author=False)


@bot.command(name="search", aliases=["buscar"])
async def search_cmd(ctx: commands.Context, *, query: str):
    """Manual web search via the configured fallback provider."""
    try:
        async with ctx.typing():
            results = await search_client.search(query)
            formatted = search_client.format_results(results)

            # Have the LLM summarise the results in its current personality.
            llm_response = await claude_client.generate_response([
                {
                    "role": "user",
                    "content": (
                        f"I searched the web for: '{query}'.\n\nResults:\n{formatted}\n\n"
                        f"Give me a short, direct summary of what came up."
                    ),
                }
            ])

            await ctx.reply(llm_response[:1900], mention_author=False)

    except Exception as e:
        print(f"[bot] !search error: {e}")
        await ctx.reply(
            "Search failed. (Check that a search API key is configured.)",
            mention_author=False,
        )


@bot.command(name="info", aliases=["lain"])
async def info_cmd(ctx: commands.Context):
    """Bot info."""
    bot_label = bot.user.name if bot.user else "Bot"
    handle = f"@{bot_label}"
    personality_id = getattr(claude_client._personality, "id", "n/a")
    info = (
        f"**{bot_label}**\n"
        f"Provider: `{claude_client.name}`\n"
        f"Model: `{claude_client.model}`\n"
        f"Personality: `{personality_id}`\n"
        f"Allowed guild: `{settings.ALLOWED_GUILD_ID or 'any'}`\n"
        f"Allowed channel: `{settings.ALLOWED_CHANNEL_ID or 'any'}`\n"
        f"History window: `{settings.HISTORY_LIMIT}` messages\n"
        f"\nMention me with {handle} to talk. "
        f"Use `{settings.BOT_PREFIX}help` for commands."
    )
    await ctx.reply(info, mention_author=False)


@bot.command(name="help", aliases=["helpbot"])
async def help_cmd(ctx: commands.Context):
    """Show help."""
    handle = f"@{bot.user.name}" if bot.user else "@bot"
    p = settings.BOT_PREFIX
    help_text = (
        f"**Commands**\n"
        f"`{handle} <message>` — chat with me; I read recent history and reply with context\n"
        f"`{p}summary [N]` — summarise the last N messages (default 50)\n"
        f"`{p}context` — send today's saved `.md` context file\n"
        f"`{p}search <query>` — manual web search\n"
        f"`{p}join` — join the voice channel\n"
        f"`{p}leave` — leave the voice channel\n"
        f"`{p}say <text>` — force the bot to speak something via TTS\n"
        f"`{p}info` — bot info (provider, model, personality)\n"
        f"`{p}help` — this message\n"
        f"\n**Tip:** mention me ({handle}) any time and I'll join the conversation with full chat context. "
        f"Natural-language phrases like “join voice channel” or “leave voice” also work."
    )
    await ctx.reply(help_text, mention_author=False)


# ─── Voice ───

# Natural-language triggers — recognised when the bot is mentioned, e.g.
# "@bot join voice channel" or "@bot get out of vc". Case-insensitive.
_JOIN_NL_RE = re.compile(
    r"\b(join|hop\s+(in|on)|come\s+(in|to)|enter|connect)\b.*"
    r"\b(voice|vc|call|voice\s+channel)\b",
    re.IGNORECASE,
)
_LEAVE_NL_RE = re.compile(
    r"\b(leave|exit|disconnect|drop|get\s+out\s+of|go|bye)\b.*"
    r"\b(voice|vc|call|voice\s+channel)\b",
    re.IGNORECASE,
)


def _voice_disabled_msg() -> str:
    if not settings.VOICE_ENABLED:
        return "La voz está desactivada (VOICE_ENABLED=false en .env)."
    if not settings.ELEVENLABS_API_KEY:
        return "ELEVENLABS_API_KEY is missing in .env — voice cannot start."
    if not VOICE_RECV_AVAILABLE:
        return "Falta el paquete `discord-ext-voice-recv`. Reinstalá las dependencias."
    return ""


async def _do_join(ctx: commands.Context) -> None:
    err = _voice_disabled_msg()
    if err:
        await ctx.reply(err, mention_author=False)
        return
    if not ctx.guild:
        await ctx.reply("This only works in a server.", mention_author=False)
        return

    vc: discord.VoiceChannel | None = None

    # 1) Si VOICE_CHANNEL_ID está configurado, usar ese canal fijo
    if settings.VOICE_CHANNEL_ID:
        ch = ctx.guild.get_channel(settings.VOICE_CHANNEL_ID)
        if ch is None:
            try:
                ch = await bot.fetch_channel(settings.VOICE_CHANNEL_ID)
            except Exception as e:
                await ctx.reply(
                    f"No encuentro el canal de voz `{settings.VOICE_CHANNEL_ID}`: {e}",
                    mention_author=False,
                )
                return
        if not isinstance(ch, discord.VoiceChannel):
            await ctx.reply(
                f"El canal `{settings.VOICE_CHANNEL_ID}` no es un canal de voz.",
                mention_author=False,
            )
            return
        vc = ch

    # 2) Otherwise, use whichever VC the calling user is currently in.
    else:
        if not isinstance(ctx.author, discord.Member) or not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.reply(
                "Join a voice channel first so I know which one to connect to. "
                "(Or set `VOICE_CHANNEL_ID` in your env to always join the same one.)",
                mention_author=False,
            )
            return
        candidate = ctx.author.voice.channel
        if not isinstance(candidate, discord.VoiceChannel):
            await ctx.reply("That isn't a voice channel — I can't connect.", mention_author=False)
            return
        vc = candidate

    try:
        await voice_manager.join(vc, ctx.channel)
        await ctx.reply(
            f"Connected to **{vc.name}**. I'm listening; talk away.",
            mention_author=False,
        )
    except Exception as e:
        print(f"[VOICE] join error: {e}")
        await ctx.reply(f"Couldn't connect: {e}", mention_author=False)


async def _do_leave(ctx: commands.Context) -> None:
    if not ctx.guild:
        return
    ok = await voice_manager.leave(ctx.guild.id)
    if ok:
        await ctx.reply("Left the voice channel. Still here in chat.", mention_author=False)
    else:
        await ctx.reply("Wasn't in any voice channel.", mention_author=False)


@bot.command(name="join")
async def join_cmd(ctx: commands.Context):
    await _do_join(ctx)


@bot.command(name="leave")
async def leave_cmd(ctx: commands.Context):
    await _do_leave(ctx)


@bot.command(name="say", aliases=["sayvoz"])
async def say_cmd(ctx: commands.Context, *, text: str):
    """Forzar TTS en el canal de voz actual."""
    if not ctx.guild or not voice_manager.is_connected(ctx.guild.id):
        await ctx.reply("I'm not in a voice channel. Use `!join` first.", mention_author=False)
        return
    session = voice_manager.get(ctx.guild.id)
    if not session:
        return
    try:
        await session.speak(text)
    except Exception as e:
        await ctx.reply(f"TTS error: {e}", mention_author=False)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    """Auto-leave the VC if the bot ends up alone (no humans)."""
    if not member.guild:
        return
    session = voice_manager.get(member.guild.id)
    if not session or not session.voice_client or not session.voice_client.is_connected():
        return
    vc = session.voice_client.channel
    if not vc:
        return
    # Count non-bot humans in the channel
    humans = [m for m in vc.members if not m.bot]
    if not humans:
        print(f"[VOICE] alone in {vc.name}, leaving")
        await voice_manager.leave(member.guild.id)


# ─── Slash commands ───
#
# Slash commands are Discord's modern command interface (autocomplete,
# argument validation, descriptions in the picker). The legacy `!prefix`
# commands above keep working for muscle memory and existing aliases.
#
# Slash commands need to be SYNCED with Discord on startup. We do that in
# `on_ready` once `bot.user` is known. If you set `ALLOWED_GUILD_ID`, sync
# is scoped to that guild for instant availability; otherwise it's a global
# sync (can take up to an hour to propagate the first time).

tree = bot.tree


def _allowed_guild_obj() -> discord.Object | None:
    if settings.ALLOWED_GUILD_ID:
        return discord.Object(id=settings.ALLOWED_GUILD_ID)
    return None


@tree.command(name="info", description="Show bot info (provider, model, personality)")
async def slash_info(interaction: discord.Interaction):
    bot_label = bot.user.name if bot.user else "Bot"
    handle = f"@{bot_label}"
    personality_id = getattr(claude_client._personality, "id", "n/a")
    info = (
        f"**{bot_label}**\n"
        f"Provider: `{claude_client.name}`\n"
        f"Model: `{claude_client.model}`\n"
        f"Personality: `{personality_id}`\n"
        f"Allowed guild: `{settings.ALLOWED_GUILD_ID or 'any'}`\n"
        f"Allowed channel: `{settings.ALLOWED_CHANNEL_ID or 'any'}`\n"
        f"History window: `{settings.HISTORY_LIMIT}` messages\n"
        f"\nMention me with {handle} to talk."
    )
    await interaction.response.send_message(info, ephemeral=False)


@tree.command(name="help", description="List available commands")
async def slash_help(interaction: discord.Interaction):
    handle = f"@{bot.user.name}" if bot.user else "@bot"
    text = (
        f"**Commands**\n"
        f"`{handle} <message>` — chat with full history context\n"
        f"`/summary [N]` — summarise the last N messages\n"
        f"`/context` — send today's saved `.md` context file\n"
        f"`/search <query>` — manual web search\n"
        f"`/join` — join the voice channel\n"
        f"`/leave` — leave the voice channel\n"
        f"`/say <text>` — speak a text via TTS\n"
        f"`/info` — bot info\n"
        f"`/help` — this message\n"
        f"\nLegacy `!prefix` commands also work as aliases."
    )
    await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="summary", description="Summarise the last N messages of this channel")
@app_commands.describe(limit="How many messages back to summarise (default 50, max 200)")
async def slash_summary(interaction: discord.Interaction, limit: int = 50):
    limit = max(1, min(200, limit))
    await interaction.response.defer(thinking=True)
    try:
        history = await context_manager.get_channel_history(interaction.channel, limit=limit)
        chat_text = "\n".join(
            f"{m['author']}: {m['content']}"
            for m in history if m['content'].strip()
        )
        response = await claude_client.analyze_conversation(
            history_text=chat_text,
            task=(
                f"Summarise the following Discord conversation. Match the tone "
                f"and language of the channel (don't translate). Cover the last "
                f"{limit} messages: who said what, what was decided, what's open."
            ),
        )
        body = response if len(response) <= 1900 else response[:1900] + "..."
        await interaction.followup.send(f"**Summary:**\n{body}")
    except Exception as e:
        print(f"[bot] /summary error: {e}")
        await interaction.followup.send("Couldn't build the summary, try again.")


@tree.command(name="context", description="Send today's saved context file")
async def slash_context(interaction: discord.Interaction):
    try:
        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = context_manager.contexts_dir / f"{date_str}.md"
        if not filepath.exists():
            await interaction.response.send_message(
                "No context saved yet today. Send me a message first.",
                ephemeral=True,
            )
            return
        if filepath.stat().st_size > 8_000_000:
            await interaction.response.send_message(
                "Today's context is too big to upload in one piece.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Today's saved context ({date_str}):",
            file=discord.File(filepath),
        )
    except Exception as e:
        print(f"[bot] /context error: {e}")
        await interaction.response.send_message(
            "Couldn't read the context file.", ephemeral=True
        )


@tree.command(name="search", description="Manual web search via the configured fallback")
@app_commands.describe(query="What to search for")
async def slash_search(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)
    try:
        results = await search_client.search(query)
        formatted = search_client.format_results(results)
        llm_response = await claude_client.generate_response([
            {
                "role": "user",
                "content": (
                    f"I searched the web for: '{query}'.\n\nResults:\n{formatted}\n\n"
                    f"Give me a short, direct summary of what came up."
                ),
            }
        ])
        await interaction.followup.send(llm_response[:1900])
    except Exception as e:
        print(f"[bot] /search error: {e}")
        await interaction.followup.send(
            "Search failed. (Check that a search API key is configured.)"
        )


@tree.command(name="join", description="Join the configured voice channel")
async def slash_join(interaction: discord.Interaction):
    # Reuse the prefix-command handler. It expects a Context; build one.
    fake_message = interaction.message or None
    if fake_message is None:
        # Defer first so Discord doesn't time out while we connect
        await interaction.response.defer(thinking=False, ephemeral=True)
    err = _voice_disabled_msg()
    if err:
        await interaction.followup.send(err, ephemeral=True)
        return
    if not interaction.guild:
        await interaction.followup.send("This only works in a server.", ephemeral=True)
        return

    vc = None
    if settings.VOICE_CHANNEL_ID:
        ch = interaction.guild.get_channel(settings.VOICE_CHANNEL_ID)
        if isinstance(ch, discord.VoiceChannel):
            vc = ch
    if vc is None and isinstance(interaction.user, discord.Member) and interaction.user.voice:
        if isinstance(interaction.user.voice.channel, discord.VoiceChannel):
            vc = interaction.user.voice.channel

    if vc is None:
        await interaction.followup.send(
            "Join a voice channel first or set `VOICE_CHANNEL_ID`.",
            ephemeral=True,
        )
        return

    try:
        await voice_manager.join(vc, interaction.channel)
        await interaction.followup.send(f"Connected to **{vc.name}**.")
    except Exception as e:
        print(f"[VOICE] /join error: {e}")
        await interaction.followup.send(f"Couldn't connect: {e}", ephemeral=True)


@tree.command(name="leave", description="Leave the voice channel")
async def slash_leave(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not interaction.guild:
        await interaction.followup.send("This only works in a server.")
        return
    ok = await voice_manager.leave(interaction.guild.id)
    if ok:
        await interaction.followup.send("Left the voice channel.")
    else:
        await interaction.followup.send("Wasn't in any voice channel.")


@tree.command(name="say", description="Force a TTS line in the current voice channel")
@app_commands.describe(text="What to speak")
async def slash_say(interaction: discord.Interaction, text: str):
    await interaction.response.defer(ephemeral=True)
    if not interaction.guild or not voice_manager.is_connected(interaction.guild.id):
        await interaction.followup.send("I'm not in a voice channel. Use `/join` first.")
        return
    session = voice_manager.get(interaction.guild.id)
    if not session:
        return
    try:
        await session.speak(text)
        await interaction.followup.send("Spoken.")
    except Exception as e:
        await interaction.followup.send(f"TTS error: {e}")


# ─── Entrypoint ───

def _run() -> int:
    """Start the bot with friendlier error messages for common misconfigs."""
    import discord.errors as derrors

    if not settings.DISCORD_BOT_TOKEN:
        print(
            "\n  \033[31m✗\033[0m DISCORD_BOT_TOKEN is empty.\n"
            "    Set it in .env or run: python3 setup.py\n",
            flush=True,
        )
        return 1

    try:
        bot.run(settings.DISCORD_BOT_TOKEN)
        return 0
    except derrors.LoginFailure:
        print(
            "\n  \033[31m✗\033[0m Discord rejected the token (LoginFailure).\n"
            "    Check DISCORD_BOT_TOKEN in .env, or regenerate the token at\n"
            "    https://discord.com/developers/applications.\n",
            flush=True,
        )
        return 1
    except derrors.PrivilegedIntentsRequired:
        print(
            "\n  \033[31m✗\033[0m Privileged intents are not enabled for this bot.\n"
            "    In https://discord.com/developers/applications → your app →\n"
            "    Bot → enable Message Content + Server Members intents, then\n"
            "    restart.\n",
            flush=True,
        )
        return 1
    except KeyboardInterrupt:
        print("\n  Stopped.")
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(_run())
