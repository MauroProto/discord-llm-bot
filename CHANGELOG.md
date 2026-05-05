# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-05-05

This release turns `discord-llm-bot` from a clone-and-configure script into a proper distributable CLI tool. Two install paths now share a single command on PATH, with first-run configuration handled by an interactive wizard.

### Added

- **`pipx install` path.** `pipx install git+https://github.com/MauroProto/discord-llm-bot.git` installs the bot into an isolated venv and exposes the `discord-llm-bot` CLI globally. Pure Python — doesn't need to clone the repo onto the host.
- **One-line `curl | bash` installer.** `curl -fsSL .../install.sh | bash` detects the OS, installs voice system deps (`ffmpeg`, `opus`, `libsodium`), creates the venv, and launches the wizard. Works on macOS, Debian, Arch, Fedora.
- **Interactive setup wizard.** Curses-based TUI (with ANSI text fallback when no TTY) that walks the user through Discord token, LLM provider, personality, voice, and deploy target — validating each step against the actual API before continuing.
- **`discord-llm-bot` CLI** with subcommands:
  - `discord-llm-bot` — start the bot
  - `discord-llm-bot setup` — re-run the wizard
  - `discord-llm-bot doctor` — read-only health check (Python, system deps, .env keys, provider reachability)
  - `discord-llm-bot from-env` — build `.env` from process environment (Docker / CI)
  - `discord-llm-bot path` — print the data dir
  - `discord-llm-bot help` — usage
- **Six providers.** New: Ollama (local, free), Codex CLI (uses your ChatGPT subscription via OpenAI's official `codex` binary). Existing: Anthropic, OpenAI, Gemini, OpenRouter.
- **MCP support.** `MCP_SERVERS_JSON` env var connects Claude to remote Model Context Protocol servers (server-side via Anthropic's beta connector). Optional per-server allow/deny via `MCP_TOOL_FILTERS_JSON`.
- **Streaming replies.** Discord messages are edited in place as the LLM generates (rate-limited to one edit per 600 ms). Toggle with `STREAMING_REPLIES`.
- **Slash commands.** Native `/info`, `/help`, `/summary`, `/context`, `/search`, `/join`, `/leave`, `/say` alongside the existing prefix commands.
- **Three personality presets** — `friendly`, `snarky`, `analyst` — with markdown frontmatter (`description:` shows in the wizard menu). Override fully via `CUSTOM_SYSTEM_PROMPT` or `CUSTOM_SYSTEM_PROMPT_FILE`.
- **Railway one-click deploy button** in the README, with the relevant env vars pre-listed.
- **CI workflow** that runs both install paths end-to-end on every push (`ubuntu-24.04`).
- **Friendly startup errors.** Missing token, login failure, and missing intents each print a one-line diagnosis instead of a 30-line discord.py traceback.
- **`SECURITY.md` extension** documenting the trust model of `curl | bash` and how to inspect the installer before running it.
- **Makefile** with the common dev targets (`install`, `setup`, `doctor`, `run`, `docker-up`, `update`, `clean`, `test`).
- **Templates package** (`templates/env_example.txt`) so the wizard's template renders correctly for both pipx and curl install paths.
- **Personalities readme** explaining the format for adding your own.

### Changed

- **Repository renamed** from `discord-claude-bot` to `discord-llm-bot`. GitHub auto-redirects the old URL.
- **Vercel landing renamed** to `discord-llm-bot.vercel.app` with copy-paste install snippets for both methods.
- **`setup.py` renamed to `wizard.py`** to free the filename for `setuptools` (so `pyproject.toml` drives the build).
- **`.env.example` placeholders** are now commented out for optional fields, so `cp .env.example .env` produces an importable file with only `DISCORD_BOT_TOKEN` left to fill.
- **`ANTHROPIC_API_KEY` is no longer required** — providers raise a clear error at instantiation if their key is missing, but the bot can run on any other provider with no Anthropic key configured.
- **All bot strings translated to English.** Old Spanish-language commands (`!resumen`, `!buscar`, `!contexto`, `!lain`, `!helpbot`, `!sayvoz`) kept as aliases for backward compatibility.
- **Memory layout standardized at `~/.discord-llm-bot/`** (override with `$DLBOT_HOME`). Both install methods write `.env` and `data/` to the same dir, so switching between pipx and curl preserves config.

### Fixed

- **Wizard crashed on EOF** (closed stdin from scripts, or Ctrl-D in interactive sessions). Now exits cleanly with the standard "Aborted. No changes written." message.
- **`install.sh` failed under `docker run` without `-t`.** TTY probe used `[ -e /dev/tty ]`, which is true even when the device can't be opened. Now tries to actually open it.
- **`Dockerfile` was missing `providers/` and `personalities/`.** `COPY *.py .` only grabbed root-level files, so the image crashed on import after the multi-provider refactor.
- **`from-env` produced unimportable `.env` files.** Template placeholders were copied verbatim, failing pydantic validation on int fields. Placeholders are now commented out in the generated file.

### Security

- Documented `curl | bash` trust model and inspection workflow in `SECURITY.md`.
- Audited deps with `pip-audit` — surfaced CVE-2025-69277 in PyNaCl 1.5.0. Pin remains `<1.6` because `discord.py 2.7.1`'s voice extra caps it there. The vulnerable code path is not reachable from the bot's threat model (we only encrypt audio frames coming from Discord's gateway, never user-supplied bytes). Will revisit when discord.py releases a version that lifts the upper bound.

### Removed

- Hardcoded "Lain" personality + Spanish-language defaults moved out of the public template (still maintained in the private `lain-bot` fork).

## [0.1.0] — pre-release

Initial public release of the multi-provider refactor. Single Anthropic provider abstracted into `LLMProvider` + `Capability`, allowing OpenAI, Gemini, and OpenRouter to slot in. Personality system. English-language strings. Slash commands. CI in place.
