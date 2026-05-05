#!/usr/bin/env bash
# discord-llm-bot — one-line installer
#
#   curl -fsSL https://raw.githubusercontent.com/MauroProto/discord-llm-bot/main/scripts/install.sh | bash
#
# Detects your OS, installs system deps (ffmpeg/opus/libsodium), clones the
# repo to ~/.discord-llm-bot, creates a venv, installs Python deps, drops a
# `discord-llm-bot` shim into ~/.local/bin, and launches the setup wizard.

set -euo pipefail

REPO_URL="${DISCORD_LLM_BOT_REPO:-https://github.com/MauroProto/discord-llm-bot.git}"
BRANCH="${DISCORD_LLM_BOT_BRANCH:-main}"
INSTALL_DIR="${DISCORD_LLM_BOT_DIR:-$HOME/.discord-llm-bot}"
BIN_DIR="${DISCORD_LLM_BOT_BIN_DIR:-$HOME/.local/bin}"

# ---------- Pretty output ----------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; MAGENTA=$'\033[35m'
else
  BOLD=""; DIM=""; RESET=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; MAGENTA=""
fi

banner() {
  echo "${MAGENTA}┌──────────────────────────────────────────────────────────┐${RESET}"
  echo "${MAGENTA}│${RESET}${BOLD}          🤖  discord-llm-bot — Installer${RESET}                ${MAGENTA}│${RESET}"
  echo "${MAGENTA}└──────────────────────────────────────────────────────────┘${RESET}"
  echo
}

ok()    { echo "${GREEN}  ✓ $*${RESET}"; }
fail()  { echo "${RED}  ✗ $*${RESET}" >&2; }
warn()  { echo "${YELLOW}  ! $*${RESET}"; }
info()  { echo "${DIM}  · $*${RESET}"; }
step()  { echo; echo "${CYAN}◆ $*${RESET}"; }

abort() { fail "$*"; exit 1; }

# ---------- OS detection ----------
detect_os() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux)
      if [ -f /etc/debian_version ]; then echo "debian"
      elif [ -f /etc/arch-release ]; then echo "arch"
      elif [ -f /etc/fedora-release ] || [ -f /etc/redhat-release ]; then echo "fedora"
      else echo "linux-other"
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
    *) echo "unknown" ;;
  esac
}

OS=$(detect_os)
banner
info "Detected OS: ${OS}"
info "Install dir: ${INSTALL_DIR}"
info "Repo:        ${REPO_URL} (branch: ${BRANCH})"

# ---------- 1. Check core tools ----------
step "Checking core tools"

need() {
  if command -v "$1" >/dev/null 2>&1; then
    ok "$1 found"
    return 0
  fi
  fail "$1 missing"
  return 1
}

MISSING_CORE=()
need git    || MISSING_CORE+=("git")
need python3 || MISSING_CORE+=("python3")

if [ ${#MISSING_CORE[@]} -gt 0 ]; then
  echo
  fail "Missing required tools: ${MISSING_CORE[*]}"
  case "$OS" in
    macos)   warn "Install with: xcode-select --install   (and/or brew install git python@3.12)" ;;
    debian)  warn "Install with: sudo apt install -y git python3 python3-venv python3-pip" ;;
    arch)    warn "Install with: sudo pacman -S git python python-pip" ;;
    fedora)  warn "Install with: sudo dnf install -y git python3 python3-pip" ;;
    *)       warn "Install git and python3 with your package manager." ;;
  esac
  abort "Re-run this installer once they're installed."
fi

PY_VER=$(python3 -c 'import sys; print("{}.{}".format(sys.version_info[0], sys.version_info[1]))')
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)')
if [ "$PY_OK" = "1" ]; then
  ok "Python ${PY_VER}"
else
  fail "Python ${PY_VER} (need 3.11+)"
  abort "Upgrade Python before continuing."
fi

# ---------- 2. System deps for voice ----------
step "Installing voice system dependencies (ffmpeg, opus, libsodium)"
info "These are optional — voice won't work without them, text chat will."

install_brew_pkgs() {
  if ! command -v brew >/dev/null 2>&1; then
    warn "Homebrew not found. Skipping voice deps. Install brew from https://brew.sh"
    return 0
  fi
  for pkg in ffmpeg opus libsodium; do
    if brew list "$pkg" >/dev/null 2>&1; then
      ok "$pkg already installed"
    else
      info "brew install $pkg"
      brew install "$pkg" >/dev/null 2>&1 && ok "$pkg installed" || warn "$pkg install failed"
    fi
  done
}

install_apt_pkgs() {
  if ! command -v apt >/dev/null 2>&1; then return 0; fi
  info "sudo apt update && sudo apt install ffmpeg libopus0 libsodium23"
  if sudo apt update -qq && sudo apt install -y ffmpeg libopus0 libsodium23 >/dev/null 2>&1; then
    ok "voice deps installed"
  else
    warn "Some voice deps failed to install. You can re-run later."
  fi
}

case "$OS" in
  macos) install_brew_pkgs ;;
  debian) install_apt_pkgs ;;
  arch) info "sudo pacman -S ffmpeg opus libsodium  (run manually if you want voice)" ;;
  fedora) info "sudo dnf install ffmpeg opus libsodium  (run manually if you want voice)" ;;
  *) warn "Unknown OS — skip system deps. Install ffmpeg/opus/libsodium manually for voice." ;;
esac

# ---------- 3. Clone or update repo ----------
step "Fetching the bot"

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Existing install found at $INSTALL_DIR — updating"
  if git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH" \
     && git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" --quiet; then
    ok "Updated to latest $BRANCH"
  else
    warn "Update failed — using existing checkout"
  fi
elif [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
  abort "$INSTALL_DIR exists and isn't a git repo. Move it aside or set DISCORD_LLM_BOT_DIR."
else
  info "Cloning into $INSTALL_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" --quiet
  ok "Cloned"
fi

cd "$INSTALL_DIR"

# ---------- 4. Python venv + deps ----------
step "Setting up Python environment"

VENV="$INSTALL_DIR/.venv"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
  ok "venv created at .venv/"
else
  ok "venv already exists"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

info "Upgrading pip"
pip install --upgrade --quiet pip

info "Installing requirements (this may take a minute)"
if pip install --pre --quiet -r requirements.txt; then
  ok "Python dependencies installed"
else
  fail "pip install failed"
  abort "Check the error above and re-run."
fi

deactivate

# ---------- 5. Install launcher into PATH ----------
step "Installing launcher"

mkdir -p "$BIN_DIR"
LAUNCHER="$BIN_DIR/discord-llm-bot"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Auto-generated launcher for discord-llm-bot
INSTALL_DIR="$INSTALL_DIR"
cd "\$INSTALL_DIR" || exit 1
# shellcheck disable=SC1091
source "\$INSTALL_DIR/.venv/bin/activate"
case "\${1:-run}" in
  setup|configure|reconfigure)
    exec python3 setup.py
    ;;
  update)
    git -C "\$INSTALL_DIR" pull --ff-only
    pip install --pre --quiet -r requirements.txt
    ;;
  run|"")
    exec python3 bot.py
    ;;
  path)
    echo "\$INSTALL_DIR"
    ;;
  *)
    exec python3 "\$@"
    ;;
esac
EOF
chmod +x "$LAUNCHER"
ok "Launcher installed at $LAUNCHER"

case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR is already on PATH" ;;
  *)
    warn "$BIN_DIR is NOT on your PATH yet."
    SHELL_NAME=$(basename "${SHELL:-}")
    case "$SHELL_NAME" in
      zsh)  RC="$HOME/.zshrc" ;;
      bash) RC="$HOME/.bashrc" ;;
      fish) RC="$HOME/.config/fish/config.fish" ;;
      *)    RC="" ;;
    esac
    if [ -n "$RC" ]; then
      info "Add this line to $RC:"
      echo "${CYAN}    export PATH=\"$BIN_DIR:\$PATH\"${RESET}"
    fi
    ;;
esac

# ---------- 6. Launch the wizard ----------
echo
echo "${MAGENTA}─────────────────────────────────────────────────────────${RESET}"
ok "Install complete. Launching the setup wizard…"
echo "${MAGENTA}─────────────────────────────────────────────────────────${RESET}"
echo

# Re-exec the wizard with a real TTY so curses works even when this script
# was invoked via `curl ... | bash`.
if [ -t 0 ] && [ -t 1 ]; then
  exec "$VENV/bin/python3" "$INSTALL_DIR/setup.py"
elif [ -e /dev/tty ]; then
  exec "$VENV/bin/python3" "$INSTALL_DIR/setup.py" < /dev/tty > /dev/tty 2>&1
else
  warn "No interactive terminal detected — skipping the wizard."
  info "Run it later with: discord-llm-bot setup"
fi
