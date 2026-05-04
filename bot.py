"""Lain Discord Bot — Core module with event handlers and commands."""

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
    print(f"Lain conectada como {bot.user} (ID: {bot.user.id})")
    print(f"Guild permitido: {settings.ALLOWED_GUILD_ID or 'Ninguno (todos)'}")
    print(f"Canal permitido: {settings.ALLOWED_CHANNEL_ID or 'Ninguno (todos)'}")
    print("Lain esta viva y lista para romper las pelotas.")


def _should_respond(message: discord.Message) -> bool:
    """Determine if Lain should respond to a message.
    
    Returns True if:
    - Bot is mentioned (@Lain)
    - Message starts with command prefix
    - Contains specific keywords (if spontaneous enabled)
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


_BOT_SELF_PREFIXES = ("Lain-bot#0013:", "Lain-bot:", "Lain:")


def _build_claude_history(history: list[dict], exclude_id: int | None = None) -> list[dict]:
    """Convert Discord history to Claude message format (without current message)."""
    messages = []
    for msg in history:
        if exclude_id is not None and msg.get("id") == exclude_id:
            continue
        if msg.get("is_bot"):
            content = msg["content"]
            for prefix in _BOT_SELF_PREFIXES:
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
    cleaned_text = message.content.replace(f"<@{bot_user_id}>", "@Lain").strip()
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
                        f"[Respondiendo a {ref_msg.author}: \"{ref_msg.content[:300]}\"]"
                    )
        except Exception as e:
            print(f"[REF] no pude leer el mensaje referenciado: {e}")

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

    # Detección por lenguaje natural: "metete al canal de voz" / "andate del canal"
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

            # Mirror a voz si Lain está conectada al VC del mismo guild
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
                        print(f"[VOZ] mirror error: {e}")
    
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
    """Ask Lain anything with full context."""
    # Triggered via !ask, but on_message handles it too. 
    # This is a fallback if the user prefers explicit command.
    await ctx.reply(
        "Che, podes preguntarme directo mencionandome (@Lain) y te respondo con contexto automaticamente. "
        "Pero si preferis usar !ask, tambien funciona. Proba con @Lain!",
        mention_author=False
    )


@bot.command(name="resumen")
async def resumen_cmd(ctx: commands.Context, limit: int = 50):
    """Summarize the last N messages."""
    try:
        async with ctx.typing():
            history = await context_manager.get_channel_history(ctx.channel, limit=limit)
            chat_text = "\n".join([
                f"{m['author']}: {m['content']}" 
                for m in history if m['content'].strip()
            ])
            
            response = await claude_client.analyze_conversation(
                history_text=chat_text,
                task=f"Resumi la siguiente conversacion de Discord en español, estilo Lain (directa, con onda, honesta). Hace un resumen util de los ultimos {limit} mensajes.",
            )
            
            if len(response) <= 2000:
                await ctx.reply(f"**Resumen de Lain:**\n{response}", mention_author=False)
            else:
                await ctx.reply(response[:1900] + "...", mention_author=False)
    
    except Exception as e:
        print(f"Error en resumen: {e}")
        await ctx.reply("Me rompi haciendo el resumen. Reintentame.", mention_author=False)


@bot.command(name="contexto")
async def contexto_cmd(ctx: commands.Context):
    """Show today's saved context file."""
    try:
        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = context_manager.contexts_dir / f"{date_str}.md"
        
        if not filepath.exists():
            await ctx.reply("Todavia no hay contexto guardado hoy. Hablame primero, capo.", mention_author=False)
            return
        
        # Send as file if it's long
        file_size = filepath.stat().st_size
        if file_size > 8000000:  # 8MB Discord limit
            await ctx.reply("El archivo de contexto es muy grande, te lo mando en partes.", mention_author=False)
            return
        
        await ctx.reply(
            f"Aca va el contexto de hoy ({date_str}):",
            file=discord.File(filepath),
            mention_author=False
        )
    
    except Exception as e:
        print(f"Error en contexto: {e}")
        await ctx.reply("No pude leer el contexto. Reintentame.", mention_author=False)


@bot.command(name="buscar")
async def buscar_cmd(ctx: commands.Context, *, query: str):
    """Manual web search via fallback."""
    try:
        async with ctx.typing():
            results = await search_client.search(query)
            formatted = search_client.format_results(results)
            
            # Also send to Claude for a witty summary
            claude_response = await claude_client.generate_response([
                {"role": "user", "content": f"Busque esto en internet: '{query}'. Resultados:\n{formatted}\n\nDame un resumen corto y directo de lo que encontre, con tu onda de Lain."}
            ])
            
            await ctx.reply(claude_response[:1900], mention_author=False)
    
    except Exception as e:
        print(f"Error en buscar: {e}")
        await ctx.reply("La busqueda fallo. Capaz no tengo API key de busqueda configurada.", mention_author=False)


@bot.command(name="lain")
async def lain_cmd(ctx: commands.Context):
    """Bot info."""
    info = (
        f"**Soy Lain** 🤘\n"
        f"Modelo: `{settings.ANTHROPIC_MODEL}`\n"
        f"Guild permitido: `{settings.ALLOWED_GUILD_ID or 'Cualquiera'}`\n"
        f"Canal permitido: `{settings.ALLOWED_CHANNEL_ID or 'Cualquiera'}`\n"
        f"Contexto: `{settings.HISTORY_LIMIT}` mensajes de historial\n"
        f"\nMencioname (@Lain) y hablamos. O usa `{settings.BOT_PREFIX}helpbot` para ver comandos."
    )
    await ctx.reply(info, mention_author=False)


@bot.command(name="helpbot")
async def helpbot_cmd(ctx: commands.Context):
    """Show help."""
    help_text = (
        f"**Comandos de Lain:**\n"
        f"`@Lain <mensaje>` — Habla conmigo, leo el historial y respondo con contexto\n"
        f"`{settings.BOT_PREFIX}resumen [N]` — Resumo los ultimos N mensajes (default 50)\n"
        f"`{settings.BOT_PREFIX}contexto` — Te mando el archivo .md de hoy con todo el contexto guardado\n"
        f"`{settings.BOT_PREFIX}buscar <query>` — Busco en internet (fallback manual)\n"
        f"`{settings.BOT_PREFIX}join` — Me meto a tu canal de voz (alias: `entra`, `vozon`)\n"
        f"`{settings.BOT_PREFIX}leave` — Salgo del canal de voz (alias: `sali`, `vozoff`, `chau`)\n"
        f"`{settings.BOT_PREFIX}sayvoz <texto>` — Forzá que diga algo por voz\n"
        f"`{settings.BOT_PREFIX}lain` — Info del bot\n"
        f"`{settings.BOT_PREFIX}helpbot` — Este mensaje\n"
        f"\n**Tip:** Mencioname (@Lain) en cualquier momento y participo de la conversacion con todo el contexto del chat. "
        f"También podés decirme en lenguaje natural «metete al canal de voz» o «andate del canal»."
    )
    await ctx.reply(help_text, mention_author=False)


# ─── Voz ───

_JOIN_NL_RE = re.compile(
    r"\b(metete|met[ée]te|entr[áa]|sumate|veni|veng[áa]s|connectate|conect[áa]te|"
    r"unite|met[ée]te al|andate al)\b.*\b(canal\s+de\s+voz|voz|vc|call|llamada)\b",
    re.IGNORECASE,
)
_LEAVE_NL_RE = re.compile(
    r"\b(salí|sali|salite|andate|chau|fuera|desconect[áa]te|salir|sal[íi] del)\b.*"
    r"\b(canal\s+de\s+voz|voz|vc|call|llamada)\b",
    re.IGNORECASE,
)


def _voice_disabled_msg() -> str:
    if not settings.VOICE_ENABLED:
        return "La voz está desactivada (VOICE_ENABLED=false en .env)."
    if not settings.ELEVENLABS_API_KEY:
        return "Falta ELEVENLABS_API_KEY en .env, no puedo hablar."
    if not VOICE_RECV_AVAILABLE:
        return "Falta el paquete `discord-ext-voice-recv`. Reinstalá las dependencias."
    return ""


async def _do_join(ctx: commands.Context) -> None:
    err = _voice_disabled_msg()
    if err:
        await ctx.reply(err, mention_author=False)
        return
    if not ctx.guild:
        await ctx.reply("Esto solo funciona en un servidor.", mention_author=False)
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

    # 2) Sino, usar el VC en el que está el usuario
    else:
        if not isinstance(ctx.author, discord.Member) or not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.reply(
                "Metete vos primero a un canal de voz, capo, así sé a cuál unirme. "
                "(o seteá VOICE_CHANNEL_ID en el .env para que entre siempre al mismo)",
                mention_author=False,
            )
            return
        candidate = ctx.author.voice.channel
        if not isinstance(candidate, discord.VoiceChannel):
            await ctx.reply("Ese canal no es de voz, no me puedo conectar.", mention_author=False)
            return
        vc = candidate

    try:
        await voice_manager.join(vc, ctx.channel)
        await ctx.reply(f"🎙️ Estoy en **{vc.name}**. Hablen tranqui que escucho y participo.", mention_author=False)
    except Exception as e:
        print(f"[VOZ] join error: {e}")
        await ctx.reply(f"No pude conectarme: {e}", mention_author=False)


async def _do_leave(ctx: commands.Context) -> None:
    if not ctx.guild:
        return
    ok = await voice_manager.leave(ctx.guild.id)
    if ok:
        await ctx.reply("Listo, me piré del canal de voz. Sigo en el chat. 👋", mention_author=False)
    else:
        await ctx.reply("No estaba en ningún canal de voz, capa.", mention_author=False)


@bot.command(name="join", aliases=["entra", "vozon", "meteteacanal"])
async def join_cmd(ctx: commands.Context):
    await _do_join(ctx)


@bot.command(name="leave", aliases=["sali", "vozoff", "chau"])
async def leave_cmd(ctx: commands.Context):
    await _do_leave(ctx)


@bot.command(name="sayvoz")
async def sayvoz_cmd(ctx: commands.Context, *, text: str):
    """Forzar TTS en el canal de voz actual."""
    if not ctx.guild or not voice_manager.is_connected(ctx.guild.id):
        await ctx.reply("No estoy en un canal de voz. Decime `!join` primero.", mention_author=False)
        return
    session = voice_manager.get(ctx.guild.id)
    if not session:
        return
    try:
        await session.speak(text)
    except Exception as e:
        await ctx.reply(f"Error de TTS: {e}", mention_author=False)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    """Si Lain queda sola en el VC, se desconecta sola."""
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
        print(f"[VOZ] me quedé sola en {vc.name}, me piro")
        await voice_manager.leave(member.guild.id)




# ─── Entrypoint ───

if __name__ == "__main__":
    bot.run(settings.DISCORD_BOT_TOKEN)
