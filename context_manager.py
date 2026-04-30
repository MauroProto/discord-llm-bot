"""Context manager: reads Discord channel history and persists to .md files."""

import discord
from datetime import datetime
from pathlib import Path
from config import settings


class ContextManager:
    """Manages chat context storage in organized markdown files."""
    
    def __init__(self):
        self.data_dir = Path(settings.DATA_DIR)
        self.contexts_dir = self.data_dir / "contexts"
        self.threads_dir = self.data_dir / "threads"
        self.contexts_dir.mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)
    
    async def get_channel_history(self, channel: discord.TextChannel, limit: int = None) -> list[dict]:
        """Fetch message history from a Discord channel.
        
        Uses async for correctly with discord.py v2+.
        """
        limit = limit or settings.HISTORY_LIMIT
        messages = []
        async for msg in channel.history(limit=limit):
            messages.append({
                "id": msg.id,
                "author": str(msg.author),
                "author_id": msg.author.id,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat(),
                "is_bot": msg.author.bot,
            })
        return list(reversed(messages))
    
    def _build_markdown(self, history: list[dict], bot_response: str | None, query: str | None) -> str:
        """Build markdown content from history and response."""
        now = datetime.now()
        lines = [
            f"## Sesion - {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "### Contexto del chat",
            "",
        ]
        
        for msg in history:
            ts = msg["timestamp"]
            author = msg["author"]
            content = msg["content"].replace("\n", " ") or "[adjunto/sin texto]"
            lines.append(f"- **{author}** ({ts}): {content}")
        
        if query:
            lines.extend([
                "",
                f"### Consulta del usuario",
                "",
                f"> {query}",
            ])
        
        if bot_response:
            lines.extend([
                "",
                "### Respuesta de Lain",
                "",
                f"{bot_response}",
            ])
        
        lines.extend(["", "---", ""])
        return "\n".join(lines)
    
    def save_daily_context(self, channel_id: int, history: list[dict], 
                          bot_response: str | None = None, query: str | None = None):
        """Save context to contexts/YYYY-MM-DD.md"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = self.contexts_dir / f"{date_str}.md"
        
        header = f"# Contexto del dia - {date_str}\n\n"
        if not filepath.exists():
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(header)
        
        md_content = self._build_markdown(history, bot_response, query)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(md_content + "\n")
        
        return filepath
    
    def save_thread_context(self, thread_id: int, history: list[dict],
                           bot_response: str | None = None, query: str | None = None):
        """Save context to threads/{thread_id}.md"""
        filepath = self.threads_dir / f"{thread_id}.md"
        
        header = f"# Thread {thread_id}\n\n"
        if not filepath.exists():
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(header)
        
        md_content = self._build_markdown(history, bot_response, query)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(md_content + "\n")
        
        return filepath
    
    async def get_or_create_context_for_channel(self, channel: discord.TextChannel):
        """Get full context: channel history + saved daily file path."""
        history = await self.get_channel_history(channel)
        return history


# Singleton instance
context_manager = ContextManager()
