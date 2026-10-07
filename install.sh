#!/usr/bin/env bash
# curl -fsSL https://raw.githubusercontent.com/EmmN/claude-voice/main/install.sh | bash
# Installs or updates claude-voice on this machine (WSL, macOS with Homebrew, or Linux): system packages, the repo in
# ~/.claude-voice, Piper + voice,
# the listener's Python environment, ~/.local/bin commands, the /speak skill and the Claude Code hooks.
set -euo pipefail
REPO="${CLAUDE_VOICE_REPO:-https://github.com/EmmN/claude-voice.git}"
DIR="${CLAUDE_VOICE_DIR:-$HOME/.claude-voice}"

echo "claude-voice installer"
if [ "$(uname -s)" = Darwin ]; then
  # macOS: Homebrew packages. coreutils gives the GNU stat/date/timeout the scripts use (lib.sh puts them first on
  # PATH), flock the speech lock; afplay and the microphone come with macOS.
  command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
  missing=()
  for pkg in ffmpeg jq coreutils flock git; do brew list --formula "$pkg" >/dev/null 2>&1 || missing+=("$pkg"); done
  [ "${#missing[@]}" -gt 0 ] && { echo "installing brew packages: ${missing[*]}"; brew install -q "${missing[@]}"; }
else
  grep -qi microsoft /proc/version 2>/dev/null || echo "note: not WSL; using PulseAudio/PipeWire for audio"
  missing=()
  for pkg in ffmpeg jq tmux sox libsox-fmt-pulse python3 curl git util-linux; do dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg"); done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "installing apt packages: ${missing[*]} (sudo)"
    sudo apt-get update -qq && sudo apt-get install -y -qq "${missing[@]}"
  fi
fi
command -v uv >/dev/null 2>&1 || { echo "installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null; }
export PATH="$HOME/.local/bin:$PATH"

if [ -d "$DIR/.git" ] && [ ! -L "$DIR" ]; then git -C "$DIR" fetch -q origin && git -C "$DIR" reset -q --hard origin/main; echo "updated $DIR"
elif [ -L "$DIR" ]; then echo "$DIR links to a checkout; leaving it as it is"
elif [ -d "$(dirname "$0")/bin" ] && [ -x "$(dirname "$0")/bin/setup" ] && [ ! -e "$DIR" ]; then ln -s "$(cd "$(dirname "$0")" && pwd)" "$DIR"; echo "linked $DIR -> $(readlink "$DIR")"
else git clone -q "$REPO" "$DIR"; echo "cloned into $DIR"; fi

"$DIR/bin/setup" "$@"   # curl … | bash -s -- --no-listener for a distro that will not run the listener
echo
if [ "${1:-}" = "--no-listener" ]; then echo "Next: start claude as usual and type /speak on; the listener runs in another distro."
else echo "Next: 'voice mic-test' (speak, hear it back), 'claude-voice start', then /speak on in any Claude Code session."; fi
echo "Hooks load at session start: open /hooks once in a running Claude Code session or start a new one."
