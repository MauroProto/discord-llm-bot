"""discord-llm-bot — main CLI entry point.

Single command that dispatches all the subcommands users actually run:

    discord-llm-bot                start the bot
    discord-llm-bot setup          interactive setup wizard
    discord-llm-bot doctor         read-only health check
    discord-llm-bot from-env       build .env from process environment
    discord-llm-bot path           print the data dir
    discord-llm-bot help           show this help

Data location: $DLBOT_HOME (default ~/.discord-llm-bot). The wizard writes
`.env` there and the bot reads it back from there. Both the `pipx install`
flow and the `curl | bash` flow converge on the same dir, so config and
saved memory survive switching install methods.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _data_dir() -> Path:
    home = os.environ.get("DLBOT_HOME")
    if home:
        return Path(home).expanduser()
    return Path.home() / ".discord-llm-bot"


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    (p / "data").mkdir(exist_ok=True)


def _print_help() -> None:
    print("discord-llm-bot — usage:")
    print()
    print("  discord-llm-bot              start the bot")
    print("  discord-llm-bot setup        interactive setup wizard")
    print("  discord-llm-bot doctor       read-only health check")
    print("  discord-llm-bot from-env     build .env from current environment")
    print("  discord-llm-bot path         print the data dir")
    print("  discord-llm-bot help         this message")
    print()
    print(f"Data dir: {_data_dir()}  (override with $DLBOT_HOME)")


def _enter_data_dir() -> Path:
    """Ensure the data dir exists and chdir there.

    Both the wizard and the bot read/write `.env` and `./data/` relative
    to cwd. Routing every subcommand through this dir means `pipx install`
    and `curl | bash` install paths share the same config + memory.
    """
    d = _data_dir()
    _ensure_dir(d)
    os.chdir(d)
    return d


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd = (args[0] if args else "run").lower()

    if cmd in ("help", "--help", "-h"):
        _print_help()
        return 0
    if cmd == "path":
        print(_data_dir())
        return 0

    # Every other subcommand needs the data dir.
    _enter_data_dir()

    if cmd in ("setup", "wizard", "configure", "reconfigure"):
        # Lazy import: the wizard is stdlib-only and starts fast, but we
        # still avoid pulling it in for `discord-llm-bot run`.
        import wizard
        return wizard.main([])
    if cmd in ("doctor", "check", "diag", "diagnose"):
        import wizard
        return wizard.cmd_doctor()
    if cmd in ("from-env", "from_env", "noninteractive", "non-interactive"):
        import wizard
        return wizard.cmd_from_env()
    if cmd in ("run", "start", ""):
        # Importing bot.py instantiates the Discord client and the
        # provider, so we delay it until we know we're starting.
        import bot
        return bot._run()

    print(f"Unknown command: {cmd}")
    print()
    _print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
