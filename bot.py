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
            print(f"[MEM] cargada memoria de fondo: {len(memory_text)} chars")

            # Generate response (web_search is enabled as a tool, Claude uses it when needed)
            response = await claude_client.generate_response(
                messages=claude_messages,
                memory_text=memory_text,
            )
            
            # Save context (only the new exchange, not full history)
            channel_name = getattr(message.channel, "name", None)
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
            
            # Send response (chunked if too long)
            if len(response) <= 2000:
                await message.reply(response, mention_author=False)
            else:
                # Split into chunks
                chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk, mention_author=False)
                    else:
                        await message.channel.send(chunk)

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
    # Cuenta humanos en el canal (excluyendo bots)
    humans = [m for m in vc.members if not m.bot]
    if not humans:
        print(f"[VOICE] me quedé sola en {vc.name}, me piro")
        await voice_manager.leave(member.guild.id)




# ─── Entrypoint ───

if __name__ == "__main__":
    bot.run(settings.DISCORD_BOT_TOKEN)
