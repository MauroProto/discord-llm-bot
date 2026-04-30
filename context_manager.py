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
    
    def _build_exchange_markdown(
        self,
        author: str | None,
        query: str | None,
        bot_response: str | None,
        channel_name: str | None = None,
    ) -> str:
        """Build markdown for a single user→Lain exchange."""
        now = datetime.now()
        lines = [f"### {now.strftime('%H:%M:%S')}"]
        if channel_name:
            lines[0] += f" — #{channel_name}"
        lines.append("")

        if query is not None:
            who = author or "usuario"
            safe_query = query.strip().replace("\n", "\n> ") or "[sin texto]"
            lines.extend([f"**{who}:**", "", f"> {safe_query}", ""])

        if bot_response is not None:
            lines.extend(["**Lain:**", "", bot_response.strip(), ""])

        lines.extend(["---", ""])
        return "\n".join(lines)

    @staticmethod
    def _ensure_header(filepath: Path, header: str) -> None:
        if not filepath.exists():
            filepath.write_text(header, encoding="utf-8")

    def save_daily_context(
        self,
        channel_id: int,
        bot_response: str | None = None,
        query: str | None = None,
        author: str | None = None,
        channel_name: str | None = None,
    ) -> Path:
        """Append a single exchange to contexts/YYYY-MM-DD.md."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = self.contexts_dir / f"{date_str}.md"
        self._ensure_header(filepath, f"# Contexto del dia - {date_str}\n\n")

        md_content = self._build_exchange_markdown(author, query, bot_response, channel_name)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(md_content + "\n")
        return filepath

    def save_thread_context(
        self,
        thread_id: int,
        bot_response: str | None = None,
        query: str | None = None,
        author: str | None = None,
        thread_name: str | None = None,
    ) -> Path:
        """Append a single exchange to threads/{thread_id}.md."""
        filepath = self.threads_dir / f"{thread_id}.md"
        title = thread_name or f"Thread {thread_id}"
        self._ensure_header(filepath, f"# {title}\n\n")

        md_content = self._build_exchange_markdown(author, query, bot_response, thread_name)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(md_content + "\n")
        return filepath
    
    async def get_or_create_context_for_channel(self, channel: discord.TextChannel):
        """Get full context: channel history + saved daily file path."""
        history = await self.get_channel_history(channel)
        return history


# Singleton instance
context_manager = ContextManager()
