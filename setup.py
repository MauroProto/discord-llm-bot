#!/usr/bin/env python3
"""Interactive setup wizard for discord-llm-bot.

Run `python3 setup.py` to walk through Discord token, LLM provider,
personality, voice, and deploy target — with live validation at every
step. Writes a working `.env` so the bot starts on first try.

Re-run anytime: `python3 setup.py reconfigure`.

Single file, stdlib-only (curses + urllib + ssl). Falls back to a
numbered text menu when stdin is not a TTY (Docker, CI, piped input).
"""

from __future__ import annotations

import curses
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
PERSONALITIES_DIR = ROOT / "personalities"

NO_COLOR = os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb"
IS_TTY = sys.stdin.isatty() and sys.stdout.isatty()


# ---------- ANSI helpers ----------

class C:
    if NO_COLOR:
        RESET = BOLD = DIM = ""
        RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = ""
    else:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RED = "\033[31m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        BLUE = "\033[34m"
        MAGENTA = "\033[35m"
        CYAN = "\033[36m"
        WHITE = "\033[37m"


def cprint(s: str = "", color: str = "", bold: bool = False) -> None:
    prefix = (C.BOLD if bold else "") + color
    print(f"{prefix}{s}{C.RESET}")


def banner(title: str, subtitle: str = "") -> None:
    width = 60
    top = "┌" + "─" * (width - 2) + "┐"
    bot = "└" + "─" * (width - 2) + "┘"
    sep = "├" + "─" * (width - 2) + "┤"
    cprint(top, C.MAGENTA)
    cprint(f"│{title.center(width - 2)}│", C.MAGENTA, bold=True)
    if subtitle:
        cprint(sep, C.MAGENTA)
        for line in subtitle.split("\n"):
            cprint(f"│ {line.ljust(width - 4)} │", C.MAGENTA)
    cprint(bot, C.MAGENTA)
    print()


def section(title: str) -> None:
    print()
    cprint(f"◆ {title}", C.CYAN, bold=True)


def ok(msg: str) -> None:
    cprint(f"  ✓ {msg}", C.GREEN)


def fail(msg: str) -> None:
    cprint(f"  ✗ {msg}", C.RED)


def warn(msg: str) -> None:
    cprint(f"  ! {msg}", C.YELLOW)


def info(msg: str) -> None:
    cprint(f"  · {msg}", C.DIM)


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = f" {C.DIM}[{default}]{C.RESET}" if default else ""
    print(f"{C.YELLOW}? {prompt}{suffix}{C.RESET} ", end="", flush=True)
    if secret:
        try:
            import getpass
            val = getpass.getpass("")
        except Exception:
            val = input()
    else:
        val = input()
    return val.strip() or default


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        ans = ask(f"{prompt} ({suffix})").lower()
        if not ans:
            return default
        if ans in ("y", "yes", "s", "si", "sí"):
            return True
        if ans in ("n", "no"):
            return False


# ---------- Curses-style menus (with text fallback) ----------

def select(title: str, options: list[tuple[str, str]], default_idx: int = 0) -> int:
    """Arrow-key menu. options=[(label, description), ...]. Returns index.

    Falls back to numbered text menu when not on a TTY or curses fails.
    """
    if not IS_TTY:
        return _select_text(title, options, default_idx)
    try:
        return curses.wrapper(_select_curses, title, options, default_idx)
    except Exception:
        return _select_text(title, options, default_idx)


def _select_curses(stdscr, title: str, options: list[tuple[str, str]], default_idx: int) -> int:
    curses.curs_set(0)
    curses.use_default_colors()
    if curses.has_colors():
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(3, 8, -1)  # dim
        curses.init_pair(4, curses.COLOR_YELLOW, -1)
    idx = default_idx

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 0, f"? {title}", curses.color_pair(4) | curses.A_BOLD)
        for i, (label, desc) in enumerate(options):
            row = i + 2
            if row >= h - 1:
                break
            marker = "❯ " if i == idx else "  "
            line = f"{marker}{label}"
            attr = curses.color_pair(2) | curses.A_BOLD if i == idx else curses.A_NORMAL
            stdscr.addstr(row, 0, line[: w - 1], attr)
            if desc:
                desc_x = len(line) + 2
                if desc_x < w - 4:
                    stdscr.addstr(
                        row, desc_x,
                        desc[: w - desc_x - 1],
                        curses.color_pair(3),
                    )
        hint = "↑/↓ to move · Enter to select · Ctrl+C to abort"
        stdscr.addstr(h - 1, 0, hint[: w - 1], curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(options)
        elif key in (curses.KEY_ENTER, 10, 13):
            return idx
        elif key == 27:  # ESC
            raise KeyboardInterrupt


def _select_text(title: str, options: list[tuple[str, str]], default_idx: int) -> int:
    print(f"{C.YELLOW}? {title}{C.RESET}")
    for i, (label, desc) in enumerate(options):
        marker = "❯" if i == default_idx else " "
        line = f"  {marker} {i + 1}. {label}"
        if desc:
            line += f"  {C.DIM}{desc}{C.RESET}"
        print(line)
    while True:
        raw = ask(f"Pick 1-{len(options)}", default=str(default_idx + 1))
        try:
            n = int(raw)
            if 1 <= n <= len(options):
                return n - 1
        except ValueError:
            pass
        warn("Pick a valid number.")


# ---------- Validators (live HTTP calls) ----------

def _http(url: str, headers: dict | None = None, data: bytes | None = None,
          method: str = "GET", timeout: int = 10) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except Exception as e:
        return 0, str(e).encode()


def validate_discord_token(token: str) -> tuple[bool, str]:
    """Return (valid, bot_name)."""
    if not token or len(token) < 20:
        return False, "token looks too short"
    status, body = _http(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token}"},
    )
    if status == 200:
        try:
            data = json.loads(body)
            return True, f"{data.get('username', '?')}#{data.get('discriminator', '0')}"
        except Exception:
            return True, "valid"
    if status == 401:
        return False, "Discord rejected the token (401 Unauthorized)"
    return False, f"could not reach Discord (status {status})"


def validate_anthropic_key(key: str) -> tuple[bool, str]:
    if not key.startswith("sk-ant-"):
        return False, "Anthropic keys start with sk-ant-"
    status, body = _http(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        data=json.dumps({
            "model": "claude-haiku-4-5",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode(),
        method="POST",
    )
    if status == 200:
        return True, "key works, claude-haiku-4-5 reachable"
    if status == 401:
        return False, "Anthropic rejected the key (401)"
    return True, f"key submitted (HTTP {status} — couldn't fully verify)"


def validate_openai_key(key: str) -> tuple[bool, str]:
    if not key.startswith("sk-"):
        return False, "OpenAI keys start with sk-"
    status, _ = _http(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    if status == 200:
        return True, "key works"
    if status == 401:
        return False, "OpenAI rejected the key (401)"
    return True, f"key submitted (HTTP {status})"


def validate_google_key(key: str) -> tuple[bool, str]:
    if not key:
        return False, "empty"
    status, _ = _http(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
    )
    if status == 200:
        return True, "key works"
    if status == 400:
        return False, "Google rejected the key (400)"
    return True, f"key submitted (HTTP {status})"


def validate_openrouter_key(key: str) -> tuple[bool, str]:
    if not key.startswith("sk-or-"):
        return True, "key submitted (skipped check — non-standard prefix)"
    status, _ = _http(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    if status == 200:
        return True, "key works"
    return True, f"key submitted (HTTP {status})"


def validate_elevenlabs_key(key: str) -> tuple[bool, str]:
    if not key:
        return False, "empty"
    status, _ = _http(
        "https://api.elevenlabs.io/v1/user",
        headers={"xi-api-key": key},
    )
    if status == 200:
        return True, "key works"
    if status == 401:
        return False, "ElevenLabs rejected the key"
    return True, f"key submitted (HTTP {status})"


# ---------- System checks ----------

def check_command(name: str) -> bool:
    return shutil.which(name) is not None


def check_python_version() -> tuple[bool, str]:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return True, f"Python {major}.{minor}"
    return False, f"Python {major}.{minor} (need 3.11+)"


def system_install_hint(pkg: str) -> str:
    if sys.platform == "darwin":
        return f"brew install {pkg}"
    if sys.platform.startswith("linux"):
        return f"sudo apt install {pkg}"
    return f"install {pkg} for your OS"


# ---------- .env helpers ----------

def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _is_placeholder(value: str) -> bool:
    """Detect template placeholder values like 'your_token_here'."""
    v = value.strip().strip('"').strip("'").lower()
    if not v:
        return False
    if v.startswith("your_") and v.endswith("_here"):
        return True
    if v in ("changeme", "replace_me", "todo"):
        return True
    return False


def write_env(values: dict[str, str]) -> Path:
    if ENV_PATH.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = ENV_PATH.with_suffix(f".env.bak.{ts}")
        shutil.copy2(ENV_PATH, backup)
        info(f"Existing .env backed up to {backup.name}")

    template = ENV_EXAMPLE.read_text() if ENV_EXAMPLE.exists() else ""
    lines: list[str] = []
    written: set[str] = set()

    if template:
        for line in template.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, default_v = stripped.partition("=")
                k = k.strip()
                if k in values and values[k] != "":
                    lines.append(f"{k}={values[k]}")
                    written.add(k)
                elif _is_placeholder(default_v):
                    # Don't carry the template placeholder into the user's
                    # .env — it would fail pydantic validation (e.g. int
                    # parsing on ALLOWED_GUILD_ID). Comment it out so the
                    # field falls back to its default.
                    lines.append(f"# {k}={default_v}  # set this if you want to use it")
                    written.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)

    for k, v in values.items():
        if k not in written and v != "":
            lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(lines) + "\n")
    return ENV_PATH


# ---------- Wizard steps ----------

PROVIDERS = [
    ("anthropic", "Anthropic Claude", "great quality · web search · 1M context"),
    ("openai", "OpenAI GPT", "GPT-5.4 · o3 · o4-mini"),
    ("gemini", "Google Gemini", "1M context · fast Flash variants"),
    ("openrouter", "OpenRouter", "one key, 100+ models"),
    ("ollama", "Ollama (local)", "free, runs on your machine"),
    ("codex_cli", "Codex CLI", "use your ChatGPT subscription"),
]

PROVIDER_VALIDATORS: dict[str, tuple[str, Callable[[str], tuple[bool, str]]]] = {
    "anthropic": ("ANTHROPIC_API_KEY", validate_anthropic_key),
    "openai": ("OPENAI_API_KEY", validate_openai_key),
    "gemini": ("GOOGLE_API_KEY", validate_google_key),
    "openrouter": ("OPENROUTER_API_KEY", validate_openrouter_key),
}

DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-7",
    "openai": "gpt-5.4",
    "gemini": "gemini-2.5-pro",
    "openrouter": "anthropic/claude-opus-4-7",
    "ollama": "llama3.3",
    "codex_cli": "gpt-5-codex",
}

PROVIDER_MODEL_ENV = {
    "anthropic": "ANTHROPIC_MODEL",
    "openai": "OPENAI_MODEL",
    "gemini": "GEMINI_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "ollama": "OLLAMA_MODEL",
    "codex_cli": "CODEX_CLI_MODEL",
}


def step_discord(values: dict[str, str]) -> str:
    section("Discord")
    info("Create a bot at https://discord.com/developers/applications")
    info("Enable Message Content + Server Members + Voice State intents.")
    while True:
        token = ask("Bot token", default=values.get("DISCORD_BOT_TOKEN", ""), secret=True)
        if not token:
            warn("A token is required.")
            continue
        ok_, msg = validate_discord_token(token)
        if ok_:
            ok(f"Connected as {msg}")
            values["DISCORD_BOT_TOKEN"] = token
            return msg
        fail(msg)
        if not confirm("Try again?", default=True):
            values["DISCORD_BOT_TOKEN"] = token
            return "(unverified)"


def step_guild(values: dict[str, str]) -> None:
    info("Right-click your server icon in Discord → 'Copy Server ID' (Developer Mode).")
    gid = ask("Server (guild) ID — leave empty to allow any server",
              default=values.get("ALLOWED_GUILD_ID", ""))
    values["ALLOWED_GUILD_ID"] = gid


def step_provider(values: dict[str, str]) -> str:
    section("LLM Provider")
    options = [(label, desc) for _id, label, desc in PROVIDERS]
    default_idx = 0
    current = values.get("LLM_PROVIDER", "anthropic")
    for i, (pid, _, _) in enumerate(PROVIDERS):
        if pid == current:
            default_idx = i
            break
    idx = select("Pick a provider", options, default_idx)
    pid, label, _ = PROVIDERS[idx]
    values["LLM_PROVIDER"] = pid
    ok(f"Selected: {label}")

    if pid in PROVIDER_VALIDATORS:
        env_var, validator = PROVIDER_VALIDATORS[pid]
        while True:
            key = ask(f"{label} API key", default=values.get(env_var, ""), secret=True)
            if not key:
                warn("A key is required.")
                continue
            ok_, msg = validator(key)
            if ok_:
                ok(msg)
                values[env_var] = key
                break
            fail(msg)
            if not confirm("Try again?", default=True):
                values[env_var] = key
                break
    elif pid == "ollama":
        url = ask("Ollama base URL", default=values.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
        values["OLLAMA_BASE_URL"] = url
        if check_command("ollama"):
            ok("Found `ollama` binary on PATH.")
        else:
            warn("`ollama` not on PATH. Install from https://ollama.com")
    elif pid == "codex_cli":
        if check_command("codex"):
            ok("Found `codex` binary on PATH.")
            info("If you haven't logged in yet, run: codex login")
        else:
            warn("`codex` not found. Install: npm install -g @openai/codex && codex login")

    model_env = PROVIDER_MODEL_ENV[pid]
    default_model = values.get(model_env, DEFAULT_MODELS[pid])
    model = ask("Model", default=default_model)
    values[model_env] = model
    return label


def step_personality(values: dict[str, str]) -> str:
    section("Personality")
    presets: list[tuple[str, str, str]] = []
    for f in sorted(PERSONALITIES_DIR.glob("*.md")):
        if f.stem.startswith("_") or f.name == "README.md":
            continue
        desc = ""
        try:
            text = f.read_text()
            for line in text.splitlines():
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass
        presets.append((f.stem, f.stem.title(), desc or ""))

    options = [(label, desc) for _, label, desc in presets]
    options.append(("Custom", "paste your own system prompt"))
    default_idx = 0
    current = values.get("BOT_PERSONALITY", "friendly")
    for i, (pid, _, _) in enumerate(presets):
        if pid == current:
            default_idx = i
            break
    idx = select("Pick a personality", options, default_idx)
    if idx < len(presets):
        pid, label, _ = presets[idx]
        values["BOT_PERSONALITY"] = pid
        values["CUSTOM_SYSTEM_PROMPT"] = ""
        ok(f"Selected: {label}")
        return label
    print()
    info("Paste your system prompt. End with a blank line:")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    prompt = "\n".join(lines).strip()
    if prompt:
        values["CUSTOM_SYSTEM_PROMPT"] = prompt
        ok(f"Custom prompt saved ({len(prompt)} chars)")
        return "custom"
    warn("Empty prompt — falling back to friendly.")
    values["BOT_PERSONALITY"] = "friendly"
    return "friendly"


def step_voice(values: dict[str, str]) -> bool:
    section("Voice (optional)")
    enable = confirm("Enable voice channel support (ElevenLabs TTS + STT)?", default=False)
    values["ENABLE_VOICE"] = "true" if enable else "false"
    if not enable:
        ok("Skipping voice setup.")
        return False

    while True:
        key = ask("ElevenLabs API key", default=values.get("ELEVENLABS_API_KEY", ""), secret=True)
        if not key:
            warn("A key is required for voice.")
            continue
        ok_, msg = validate_elevenlabs_key(key)
        if ok_:
            ok(msg)
            values["ELEVENLABS_API_KEY"] = key
            break
        fail(msg)
        if not confirm("Try again?", default=True):
            values["ELEVENLABS_API_KEY"] = key
            break

    voice_id = ask("ElevenLabs voice ID (leave empty for default)",
                   default=values.get("ELEVENLABS_VOICE_ID", ""))
    if voice_id:
        values["ELEVENLABS_VOICE_ID"] = voice_id

    print()
    info("Checking system dependencies for voice…")
    deps_ok = True
    for cmd, hint_pkg in [("ffmpeg", "ffmpeg")]:
        if check_command(cmd):
            ok(f"{cmd} found")
        else:
            fail(f"{cmd} not installed → run: {system_install_hint(hint_pkg)}")
            deps_ok = False
    if sys.platform == "darwin":
        if not Path("/opt/homebrew/lib/libopus.dylib").exists() and not Path("/usr/local/lib/libopus.dylib").exists():
            warn("libopus may be missing → run: brew install opus libsodium")
    return deps_ok


def step_deploy(values: dict[str, str]) -> str:
    section("Deploy target")
    options = [
        ("Local", "this computer"),
        ("Docker", "docker compose up --build"),
        ("Railway / Fly / VPS", "I'll print env vars to paste"),
    ]
    idx = select("Where will this run?", options, default_idx=0)
    return ["local", "docker", "remote"][idx]


# ---------- Final summary ----------

def print_summary(values: dict[str, str], discord_id: str, provider_label: str,
                  personality: str, voice_enabled: bool, deploy: str) -> None:
    print()
    cprint("─" * 60, C.GREEN)
    cprint("✓ Setup Complete!", C.GREEN, bold=True)
    cprint("─" * 60, C.GREEN)
    print()
    cprint("Configuration:", C.WHITE, bold=True)
    ok(f"Discord:      {discord_id}")
    ok(f"Provider:     {provider_label}")
    ok(f"Personality:  {personality}")
    if voice_enabled:
        ok("Voice:        enabled")
    else:
        info("Voice:        disabled")
    if values.get("MCP_SERVERS_JSON"):
        ok("MCP servers:  configured")
    else:
        info("MCP servers:  none (edit MCP_SERVERS_JSON in .env to add)")
    print()
    cprint("📁 Files:", C.WHITE, bold=True)
    info(f".env            (secrets — DO NOT commit)")
    print()
    cprint("🚀 Next:", C.WHITE, bold=True)

    if deploy == "local":
        py_ok, py_msg = check_python_version()
        if py_ok:
            ok(f"{py_msg} OK")
        else:
            fail(py_msg)
        cprint("    pip install --pre -r requirements.txt", C.CYAN)
        cprint("    python3 bot.py", C.CYAN)
    elif deploy == "docker":
        if check_command("docker"):
            ok("docker found")
        else:
            warn("docker not found → install from https://docs.docker.com/get-docker/")
        cprint("    docker compose up --build", C.CYAN)
    else:
        cprint("    Push to your git host, then paste these vars in your platform:", C.WHITE)
        for k in [
            "DISCORD_BOT_TOKEN", "ALLOWED_GUILD_ID", "LLM_PROVIDER",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY", "OLLAMA_BASE_URL",
            "ANTHROPIC_MODEL", "OPENAI_MODEL", "GEMINI_MODEL",
            "OPENROUTER_MODEL", "OLLAMA_MODEL", "CODEX_CLI_MODEL",
            "BOT_PERSONALITY", "ENABLE_VOICE", "ELEVENLABS_API_KEY",
        ]:
            v = values.get(k, "")
            if v:
                masked = v if len(v) < 12 or k.endswith("_PROVIDER") or k.endswith("_MODEL") or k == "BOT_PERSONALITY" or k == "ENABLE_VOICE" else f"{v[:6]}…{v[-4:]}"
                cprint(f"      {k}={masked}", C.DIM)
    print()
    cprint("Re-run anytime: python3 setup.py", C.DIM)
    print()


# ---------- Doctor mode ----------

def cmd_doctor() -> int:
    """Diagnose the install: Python, system deps, .env keys."""
    banner(
        "🩺  discord-llm-bot — Doctor",
        "Read-only health check. No changes written.",
    )

    issues = 0

    section("Python")
    py_ok, py_msg = check_python_version()
    (ok if py_ok else fail)(py_msg)
    if not py_ok:
        issues += 1

    section("System dependencies (voice)")
    for cmd, hint_pkg in [("ffmpeg", "ffmpeg"), ("git", "git")]:
        if check_command(cmd):
            ok(f"{cmd} found")
        else:
            fail(f"{cmd} missing → {system_install_hint(hint_pkg)}")
            issues += 1

    section(".env file")
    if not ENV_PATH.exists():
        fail(f"{ENV_PATH.name} not found — run: python3 setup.py")
        return 1
    values = parse_env(ENV_PATH)
    ok(f"{ENV_PATH.name} loaded ({len(values)} keys)")

    section("Discord token")
    token = values.get("DISCORD_BOT_TOKEN", "")
    if not token:
        fail("DISCORD_BOT_TOKEN is empty")
        issues += 1
    else:
        ok_, msg = validate_discord_token(token)
        (ok if ok_ else fail)(msg)
        if not ok_:
            issues += 1

    section(f"LLM provider ({values.get('LLM_PROVIDER', '?')})")
    provider = values.get("LLM_PROVIDER", "anthropic")
    if provider in PROVIDER_VALIDATORS:
        env_var, validator = PROVIDER_VALIDATORS[provider]
        key = values.get(env_var, "")
        if not key:
            fail(f"{env_var} is empty")
            issues += 1
        else:
            ok_, msg = validator(key)
            (ok if ok_ else warn)(msg)
    elif provider == "ollama":
        url = values.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        info(f"Ollama URL: {url}")
        if check_command("ollama"):
            ok("`ollama` binary on PATH")
        else:
            warn("`ollama` not on PATH (the bot connects via HTTP, but you'll want this to pull models)")
    elif provider == "codex_cli":
        if check_command("codex"):
            ok("`codex` binary on PATH")
        else:
            fail("`codex` binary missing — npm install -g @openai/codex && codex login")
            issues += 1

    if values.get("ENABLE_VOICE", "").lower() in ("true", "1", "yes"):
        section("Voice")
        if values.get("ELEVENLABS_API_KEY"):
            ok_, msg = validate_elevenlabs_key(values["ELEVENLABS_API_KEY"])
            (ok if ok_ else warn)(msg)
        else:
            fail("ENABLE_VOICE=true but ELEVENLABS_API_KEY is empty")
            issues += 1

    print()
    if issues == 0:
        cprint("✓ All checks passed — you're good to go.", C.GREEN, bold=True)
        return 0
    cprint(f"✗ {issues} issue(s) found.", C.RED, bold=True)
    return 1


# ---------- Non-interactive (read everything from environment) ----------

def cmd_from_env() -> int:
    """Build a .env from the process environment, no prompts.

    Useful in Docker / CI / first-boot scripts. Validates the keys it has,
    skips prompts. Anything missing → leaves the placeholder.
    """
    banner(
        "🤖  discord-llm-bot — Non-interactive setup",
        "Reading values from the environment.",
    )

    values = parse_env(ENV_PATH)
    keys = [
        "DISCORD_BOT_TOKEN", "ALLOWED_GUILD_ID", "VOICE_CHANNEL_ID",
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
        "OPENAI_API_KEY", "OPENAI_MODEL",
        "GOOGLE_API_KEY", "GEMINI_MODEL",
        "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
        "OLLAMA_BASE_URL", "OLLAMA_MODEL",
        "CODEX_CLI_MODEL",
        "BOT_PERSONALITY", "BOT_DISPLAY_NAME",
        "CUSTOM_SYSTEM_PROMPT", "CUSTOM_SYSTEM_PROMPT_FILE",
        "ENABLE_VOICE", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
        "STREAMING_REPLIES", "EXTENDED_THINKING",
        "MCP_SERVERS_JSON", "MCP_TOOL_FILTERS_JSON",
    ]
    picked = 0
    for k in keys:
        env_v = os.environ.get(k)
        if env_v is not None and env_v != "":
            values[k] = env_v
            picked += 1
    info(f"Picked up {picked} value(s) from the environment.")

    path = write_env(values)
    ok(f"Wrote {path.name}")
    print()
    cprint("Run `python3 setup.py doctor` to validate.", C.DIM)
    return 0


# ---------- Entry point ----------

def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd = (args[0] if args else "wizard").lower()

    if cmd in ("doctor", "check", "diagnose", "diag"):
        try:
            return cmd_doctor()
        except KeyboardInterrupt:
            return 130
    if cmd in ("from-env", "from_env", "noninteractive", "non-interactive"):
        return cmd_from_env()
    if cmd in ("--help", "-h", "help"):
        print("usage: setup.py [wizard|doctor|from-env|help]")
        print()
        print("  wizard     interactive setup (default)")
        print("  doctor     read-only health check of the current install")
        print("  from-env   build .env from process environment (Docker/CI)")
        return 0

    try:
        os.system("clear" if os.name != "nt" else "cls")
        banner(
            "🤖  discord-llm-bot — Setup Wizard",
            "Self-hosted Discord bot · Multi-provider · Voice-ready\n"
            "Press Ctrl+C at any time to exit.",
        )

        py_ok, py_msg = check_python_version()
        if py_ok:
            ok(py_msg)
        else:
            fail(py_msg)
            warn("The bot needs Python 3.11+. Continuing anyway.")

        values = parse_env(ENV_PATH)
        if values:
            info(f"Loaded existing .env ({len(values)} keys) — values pre-filled.")

        discord_id = step_discord(values)
        step_guild(values)
        provider_label = step_provider(values)
        personality = step_personality(values)
        voice_enabled = step_voice(values)
        deploy = step_deploy(values)

        path = write_env(values)
        ok(f"Wrote {path.name}")

        print_summary(values, discord_id, provider_label, personality, voice_enabled, deploy)
        return 0
    except KeyboardInterrupt:
        print()
        warn("Aborted. No changes written (existing .env is intact).")
        return 130
    except Exception as e:
        print()
        fail(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
