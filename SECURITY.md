# Security policy

## Supported versions

Only the `main` branch is actively maintained. Open an issue if you depend on
an older release and need a backport.

## Reporting a vulnerability

**Please do not open public GitHub issues for security problems.**

Instead, email the maintainer directly with:

- A description of the vulnerability and its impact.
- Steps to reproduce or proof-of-concept.
- Affected commit(s) or version(s).
- Whether the issue has been disclosed elsewhere.

You can expect:

- Acknowledgement within 72 hours.
- A coordinated disclosure timeline (typically 90 days, faster for critical bugs).
- Credit in the release notes once a fix ships, unless you prefer to remain anonymous.

## Threat model

This project runs as a Discord bot with access to:

- A Discord bot token (privileged — can read/send messages and join voice channels in the configured guild).
- An Anthropic API key (billable).
- An ElevenLabs API key (billable).
- Optional: Tavily / SerpAPI keys (billable).
- Local filesystem (`./data/`) for `.md` chat history.

### What is in scope

- Leaking any of the API tokens listed above.
- Privilege escalation that lets the bot act outside its configured `ALLOWED_GUILD_ID` / `ALLOWED_CHANNEL_ID`.
- Remote code execution through bot inputs (Discord messages, voice transcripts).
- Path traversal or injection via stored context files.
- Dependency vulnerabilities that affect deployed instances.

### What is out of scope

- Abuse by users with administrative access to the bot's host or `.env` file.
- Denial-of-service via spam in the bot's allowed channel (rate limits are Discord's responsibility).
- Costs racked up by intentional misuse (set spending limits in your provider dashboards).

## Secret hygiene

- `.env` is in `.gitignore` and **must never** be committed.
- Use platform secret managers (Railway Variables, GitHub Secrets, etc.) in production.
- Rotate any key that has been exposed, even if only briefly.
- Treat the Discord bot token as equivalent to the bot's identity — anyone who has it can impersonate the bot.

## Hardening recommendations

- Always set `ALLOWED_GUILD_ID` (and ideally `ALLOWED_CHANNEL_ID`) so the bot only responds where you intend.
- Run the bot in a sandboxed environment (container, VM, or restricted user) — it executes with whatever privileges its process has.
- Keep dependencies up to date (`pip install --upgrade --pre -r requirements.txt`).
- Monitor your provider dashboards for unexpected usage spikes.

## About the `curl | bash` installer

The README recommends:

```bash
curl -fsSL https://raw.githubusercontent.com/MauroProto/discord-llm-bot/main/scripts/install.sh | bash
```

This pattern is convenient but asks you to trust this repo and GitHub's TLS chain. If you'd rather inspect before executing — recommended for any `curl | bash` from any project, ours included — do this:

```bash
# 1. Fetch the script
curl -fsSL -o install.sh \
  https://raw.githubusercontent.com/MauroProto/discord-llm-bot/main/scripts/install.sh

# 2. Read it (it's ~250 lines of plain Bash, no obfuscation)
less install.sh

# 3. Run it once you're satisfied
bash install.sh
```

What the installer touches on your machine:

| Path | What gets written |
|---|---|
| `~/.discord-llm-bot/` | Cloned repo + Python venv (override with `DISCORD_LLM_BOT_DIR`) |
| `~/.local/bin/discord-llm-bot` | Launcher shim (override with `DISCORD_LLM_BOT_BIN_DIR`) |
| System packages | `ffmpeg`, `opus`/`libopus0`, `libsodium` via `brew` or `apt` (will prompt for `sudo` on Linux) |

It does **not** modify your shell profile, write outside the dirs above, or reach out to any non-GitHub host. It pulls Python deps from PyPI — review `requirements.txt` if you want to audit those.

The installer pins to the `main` branch. If you want a specific commit, set `DISCORD_LLM_BOT_BRANCH=<sha>` before running.
