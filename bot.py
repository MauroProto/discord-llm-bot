"""Lain Discord Bot — Core module with event handlers and commands."""

import os
import random
from collections import deque
import discord
from discord.ext import commands
from datetime import datetime

from config import settings
from context_manager import context_manager
from claude_client import claude_client
from search_client import search_client

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

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


def _build_claude_messages(history: list[dict], current_author: str, current_content: str) -> list[dict]:
    """Convert Discord history to Claude message format."""
    messages = []
    for msg in history:
        role = "assistant" if msg.get("is_bot") else "user"
        content = f"{msg['author']}: {msg['content']}"
        messages.append({"role": role, "content": content})
    
    # Add current message
    messages.append({
        "role": "user",
        "content": f"{current_author}: {current_content}"
    })
    
    return messages


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

    try:
        async with message.channel.typing():
            # Fetch channel history
            history = await context_manager.get_channel_history(message.channel)

            # Build messages for Claude (Claude decides if it needs to web_search)
            claude_messages = _build_claude_messages(
                history,
                str(message.author),
                message.content.replace(f"<@{bot.user.id}>", "@Lain").strip()
            )

            # Generate response (web_search is enabled as a tool, Claude uses it when needed)
            response = await claude_client.generate_response(messages=claude_messages)
            
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
        f"`{settings.BOT_PREFIX}lain` — Info del bot\n"
        f"`{settings.BOT_PREFIX}helpbot` — Este mensaje\n"
        f"\n**Tip:** Mencioname (@Lain) en cualquier momento y participo de la conversacion con todo el contexto del chat."
    )
    await ctx.reply(help_text, mention_author=False)


# ─── Entrypoint ───

if __name__ == "__main__":
    bot.run(settings.DISCORD_BOT_TOKEN)
