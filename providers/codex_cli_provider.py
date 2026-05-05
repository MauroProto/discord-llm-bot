"""Codex CLI provider — use your ChatGPT subscription via the local `codex` binary.

This provider spawns OpenAI's official Codex CLI (https://github.com/openai/codex)
as a child process and reads its JSON event stream from stdout. The Codex CLI
holds your ChatGPT Plus / Pro / Business subscription session (after `codex
login`), so requests count against your subscription quota — no separate API
key needed.

Use this when:
- You already pay for ChatGPT Plus/Pro and want to reuse that quota.
- You're self-hosting on a machine where the `codex` CLI is installed and
  you've run `codex login` (or `codex login --device-auth` on a headless box).

Trade-offs:
- Requires the `codex` binary on PATH on the host running the bot. Won't work
  out-of-the-box on Railway / generic Docker without baking the binary into
  the image AND seeding `~/.codex/auth.json` (which expires periodically).
- Subscription quotas are tighter than API. Heavy use will hit limits.
- Latency is slightly higher than direct API (process spawn overhead).

This implementation follows the same approach used by the dccodex project
(https://github.com/Leonxlnx/dccodex) — spawn the binary, pipe the prompt to
stdin, parse `--json` events from stdout.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import Any

from config import settings

from .base import Capability, LLMProvider, resolve_personality, warn_if_mcp_configured


_URL_RE = re.compile(r"https?://\S+")


def _clean_err(e: Exception) -> str:
    text = str(e).split("\n")[0]
    text = _URL_RE.sub("", text)
    text = text.replace("`", "").strip()
    return text[:180] if text else type(e).__name__


def _flatten_message(content: Any) -> str:
    """Convert Anthropic-style message content to plain text (Codex CLI doesn't
    accept multimodal blocks; we serialise everything to text)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "image":
            parts.append("[image attachment — not visible to Codex CLI]")
        elif btype == "document":
            parts.append("[PDF attachment — not visible to Codex CLI]")
        elif "text" in block:
            parts.append(str(block["text"]))
    return "\n\n".join(parts)


def _build_prompt(messages: list[dict], system: str, memory_text: str) -> str:
    """Codex CLI takes a single prompt string. We serialise the conversation
    into a chat-transcript format so the model sees the full context."""
    lines: list[str] = []
    if system:
        lines.append(f"# System\n{system}")
    if memory_text:
        lines.append(
            "# Long-term memory (saved conversations)\n"
            "Use this as background. Do not mention it explicitly.\n\n"
            + memory_text
        )
    lines.append("# Conversation")
    for m in messages:
        role = m.get("role", "user")
        content = _flatten_message(m.get("content", ""))
        if not content.strip():
            continue
        label = "Assistant" if role == "assistant" else "User"
        lines.append(f"\n## {label}\n{content}")
    lines.append("\n## Assistant\n")  # cue the model to continue
    return "\n".join(lines)


class CodexCliProvider(LLMProvider):
    """Spawn the local `codex` CLI binary to run completions with subscription auth."""

    name = "codex_cli"

    def __init__(self) -> None:
        binary = settings.CODEX_CLI_BIN or shutil.which("codex")
        if not binary:
            raise RuntimeError(
                "Codex CLI provider needs the `codex` binary on PATH. Install it "
                "with `npm install -g @openai/codex` and run `codex login` "
                "(or `codex login --device-auth` on a headless server). Then set "
                "CODEX_CLI_BIN explicitly if it's not on PATH."
            )
        self.binary = binary
        self.model = settings.CODEX_CLI_MODEL or "gpt-5-codex"
        self.max_tokens = settings.MAX_TOKENS

        # The Codex CLI uses its own working directory for each session. By
        # default we let it run wherever the bot process is, which is usually
        # fine for chat use. Override via CODEX_CLI_WORKDIR if needed.
        self.workdir = settings.CODEX_CLI_WORKDIR or os.getcwd()

        self._personality = resolve_personality(
            bot_name=settings.BOT_DISPLAY_NAME or "the bot",
        )
        self.system_prompt = self._personality.chat_prompt
        warn_if_mcp_configured(self.name)

    # ------- Capabilities -------

    def supports(self, capability: Capability) -> bool:
        return capability in {
            Capability.STREAMING,
            Capability.REASONING,  # Codex CLI defaults to gpt-5-codex which reasons
        }

    # ------- Streaming -------

    async def stream_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ):
        prompt = _build_prompt(messages, self.system_prompt, memory_text)

        cmd = [
            self.binary,
            "exec",
            "--json",
            "--skip-git-repo-check",  # don't require a repo for chat use
            "-m", self.model,
            "-",  # read prompt from stdin
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir,
            )
        except Exception as e:
            yield f"Codex CLI failed to start: {_clean_err(e)}"
            return

        # Send the prompt
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception as e:
            yield f"Codex CLI stdin error: {_clean_err(e)}"
            try:
                proc.kill()
            except Exception:
                pass
            return

        # Read the JSON event stream and emit text deltas as they arrive.
        # Codex events of interest (subset):
        #   {"type":"item.completed","item":{"item_type":"agent_message","text":"..."}}
        #   {"type":"item.delta","item":{"text":"..."}}    (token deltas, when present)
        #   {"type":"turn.completed", ...}
        # We yield text from `agent_message` items (final) and from any
        # incremental `item.delta` events. Tool/sandbox events are ignored.
        emitted_any = False
        emitted_text: list[str] = []
        try:
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                etype = event.get("type", "")
                if etype.endswith(".delta"):
                    delta = (event.get("delta") or event.get("item", {}).get("text") or "")
                    if delta:
                        emitted_any = True
                        emitted_text.append(delta)
                        yield delta
                elif etype == "item.completed":
                    item = event.get("item", {}) or {}
                    if item.get("item_type") == "agent_message":
                        text = item.get("text", "") or ""
                        if text and not emitted_any:
                            # No deltas were emitted; fall back to the final.
                            yield text
                elif etype == "turn.failed" or etype == "error":
                    err = event.get("error") or event.get("message") or "unknown"
                    yield f"\n\n[codex error: {err}]"
        except Exception as e:
            yield f"\n\n[codex stream error: {_clean_err(e)}]"

        # Drain stderr if anything ended up there for diagnostics
        try:
            stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
            if stderr.strip():
                # Most stderr chatter is fine ("loaded X tokens..."). Only
                # surface lines that look like real errors.
                for line in stderr.splitlines():
                    if "error" in line.lower() or "failed" in line.lower():
                        yield f"\n\n[codex stderr: {line.strip()[:200]}]"
                        break
        except Exception:
            pass

        try:
            await proc.wait()
        except Exception:
            pass

    # ------- Non-streaming chat (collect the stream) -------

    async def generate_response(
        self,
        messages: list[dict],
        use_search: bool = False,
        search_query: str | None = None,
        memory_text: str = "",
    ) -> str:
        parts: list[str] = []
        async for chunk in self.stream_response(
            messages=messages,
            use_search=use_search,
            search_query=search_query,
            memory_text=memory_text,
        ):
            if chunk:
                parts.append(chunk)
        return "".join(parts).strip()

    # ------- Voice -------

    async def generate_voice_response(
        self,
        messages: list[dict],
        memory_text: str = "",
        max_chars: int | None = None,
    ) -> str:
        max_chars = max_chars or settings.VOICE_MAX_RESPONSE_CHARS

        original_system = self.system_prompt
        original_model = self.model
        try:
            self.system_prompt = original_system + self._build_voice_suffix()
            voice_model = (settings.VOICE_CODEX_CLI_MODEL or "").strip()
            if voice_model:
                self.model = voice_model
            response = await self.generate_response(
                messages=messages, memory_text=memory_text,
            )
        finally:
            self.system_prompt = original_system
            self.model = original_model

        if len(response) > max_chars:
            cut = response[:max_chars].rsplit(".", 1)[0]
            response = (cut + ".") if cut else response[:max_chars]
        return response

    # ------- Conversation analysis -------

    async def analyze_conversation(self, history_text: str, task: str) -> str:
        return await self.generate_response([
            {"role": "user", "content": f"{task}\n\nConversation:\n{history_text}"},
        ])


__all__ = ["CodexCliProvider"]
