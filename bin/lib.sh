# Shared helpers for the voice scripts (sourced, not executed).
#
# Platform layer: PLATFORM is wsl, mac or linux (override with CLAUDE_VOICE_PLATFORM). Only VOICE_SHARED and
# VOICE_LOCK know where state lives per platform; everything else (registry, inbox, stop file, player queue) derives
# from them. On WSL they sit in the Windows user folder so every distro shares them; elsewhere in ~/.claude/voice.
if [ -n "${CLAUDE_VOICE_PLATFORM:-}" ]; then PLATFORM="$CLAUDE_VOICE_PLATFORM"
elif [ "$(uname -s)" = Darwin ]; then PLATFORM=mac
elif grep -qi microsoft /proc/version 2>/dev/null; then PLATFORM=wsl
else PLATFORM=linux; fi
if [ "$PLATFORM" = mac ]; then
  # GNU stat/date/timeout and flock from Homebrew, set here (not in your shell) so hooks get them too
  for p in /opt/homebrew /usr/local; do
    [ -d "$p/opt/coreutils/libexec/gnubin" ] && PATH="$p/opt/coreutils/libexec/gnubin:$p/bin:$PATH" && break
  done
fi
VOICE_HOME="${VOICE_HOME:-$HOME/.claude/voice}"
# persistent settings: KEY=value lines in ~/.claude/voice/config (`claude-voice config KEY value`); a variable already
# set in the environment wins
if [ -f "$VOICE_HOME/config" ]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue;; esac
    k=$(printf '%s' "$k" | tr -cd 'A-Za-z0-9_'); [ -n "$k" ] || continue
    eval "[ -n \"\${$k+x}\" ]" || export "$k=$v"
  done < "$VOICE_HOME/config"
fi
VOICE_VENV="${VOICE_VENV:-$HOME/.local/share/claude-voice/venv}"
mkdir -p "$VOICE_HOME/sessions"
win_user_dir() { for u in /mnt/c/Users/*/; do case "$u" in */Public/|*/Default*/|*/All\ Users/) continue;; esac; [ -w "$u" ] && { printf '%s' "${u%/}"; return; }; done; printf '%s' "$HOME"; }
if [ "$PLATFORM" = wsl ]; then
  VOICE_SHARED="${VOICE_SHARED:-$(win_user_dir)/.claude-voice}"; VOICE_LOCK="${VOICE_LOCK:-$(win_user_dir)/.claude-voice.lock}"
  DISTRO="${WSL_DISTRO_NAME:-$(hostname)}"
else
  VOICE_SHARED="${VOICE_SHARED:-$VOICE_HOME/shared}"; VOICE_LOCK="${VOICE_LOCK:-$VOICE_HOME/speech.lock}"
  DISTRO="$(hostname -s 2>/dev/null || hostname)"
fi
mkdir -p "$VOICE_SHARED/sessions" 2>/dev/null || true
export PLATFORM VOICE_SHARED VOICE_LOCK   # listen.py and the Python heredocs read them

pid_alive() { kill -0 "$1" 2>/dev/null; }   # no /proc on macOS
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0; }          # whole seconds
mtime_ns() {   # fractional seconds; the stop-file checks rely on it, so never fail silently
  stat -c %.9Y "$1" 2>/dev/null || python3 -c 'import os, sys; print(os.stat(sys.argv[1]).st_mtime)' "$1" 2>/dev/null || echo 0
}
now_ns() { date +%s.%N 2>/dev/null | grep -v N || python3 -c 'import time; print(time.time())'; }
newer_or_equal() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a >= b) }'; }
# microphone input for ffmpeg; VOICE_MIC picks the device (PulseAudio source, or an avfoundation index on macOS).
# The capture is tagged so `listen --stop` can find it on every platform without matching anything else.
mic_input_args() {
  case "$PLATFORM" in
    mac) printf '%s\n' -f avfoundation -i ":${VOICE_MIC:-0}" ;;
    wsl) printf '%s\n' -f pulse -i "${VOICE_MIC:-RDPSource}" ;;
    *)   printf '%s\n' -f pulse -i "${VOICE_MIC:-default}" ;;
  esac
}
CAPTURE_TAG="claude-voice-capture"
venv_python() {
  local libs; libs=$(find "$VOICE_VENV/lib" -path "*nvidia/*/lib" -type d 2>/dev/null | tr '\n' ':')
  LD_LIBRARY_PATH="${libs}${LD_LIBRARY_PATH:-}" exec "$VOICE_VENV/bin/python" "$@"
}
session_name_for_id() {  # session id → friendly name (voice registry first, then Claude's own session registry)
  python3 - "$1" "$VOICE_SHARED/sessions" <<'PY'
import glob, json, sys, os
sid, reg = sys.argv[1], sys.argv[2]
for p in glob.glob(os.path.join(reg, "*.json")):
    try:
        d = json.load(open(p))
        if d.get("session_id") == sid: print(d.get("name", "")); sys.exit()
    except Exception: pass
for p in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
    try:
        d = json.load(open(p))
        if d.get("sessionId") == sid: print(d.get("name", "")); sys.exit()
    except Exception: pass
print("")
PY
}
VOICE_INBOX="$VOICE_SHARED/inbox"; mkdir -p "$VOICE_INBOX" 2>/dev/null || true
register_session() {  # sid [key=value …] → shared registry entry <distro>__<sid>.json: name and pid from Claude's own
  # session file, merged with what the entry already holds (status, last_prompt, last_reply) and the given fields
  python3 - "$VOICE_SHARED/sessions" "$DISTRO" "$@" <<'PY'
import glob, json, os, sys, time
reg, distro, sid, extra = sys.argv[1], sys.argv[2], sys.argv[3], dict(a.split("=", 1) for a in sys.argv[4:] if "=" in a)
for p in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
    try: d = json.load(open(p))
    except Exception: continue
    if d.get("sessionId") != sid: continue
    try: os.kill(int(d.get("pid")), 0)          # alive? (no /proc on macOS)
    except (OSError, TypeError, ValueError): continue
    name = d.get("name") or os.path.basename(d.get("cwd", "")) or sid[:8]
    out = os.path.join(reg, f"{distro}__{sid}.json")
    try: old = json.load(open(out))
    except Exception: old = {}
    entry = old | {"name": name, "distro": distro, "session_id": sid, "pid": d["pid"], "cwd": d.get("cwd", "")} | extra
    if extra: entry["updated"] = int(time.time())
    if entry != old:
        json.dump(entry, open(out + ".tmp", "w")); os.replace(out + ".tmp", out)
    print(name); sys.exit()
sys.exit(1)
PY
}
unregister_session() {  # session id → remove everything voice keeps for it in this distro and the shared folder
  [ "$(cat "$VOICE_SHARED/jarvis_brain" 2>/dev/null)" = "${DISTRO}__$1" ] && rm -f "$VOICE_SHARED/jarvis_brain"
  rm -f "$VOICE_SHARED/sessions/${DISTRO}__$1.json" "$VOICE_INBOX/${DISTRO}__$1.msg" "$VOICE_HOME/sessions/$1" "$VOICE_HOME/sessions/.$1.waiter"
}
PLAYER_QUEUE="$VOICE_SHARED/play"
speech_log() { printf '%s %s\n' "$(date +%T.%2N)" "$*" >> "$VOICE_HOME/speech.log" 2>/dev/null; }   # timings of each reply
player_alive() {  # WSL only: the Windows-side player (windows/player.ps1) writes player.alive every second
  [ "$PLATFORM" = wsl ] || return 1
  local f="$VOICE_SHARED/player.alive"; [ -f "$f" ] && [ $(( $(date +%s) - $(mtime "$f") )) -le 3 ]
}
start_player() {  # launch it hidden and detached; PowerShell can take 15 s to come up, so this never waits for it
  local ps1="$VOICE_SHARED/player.ps1" mark="$VOICE_SHARED/player.starting"
  [ "$PLATFORM" = wsl ] && command -v powershell.exe >/dev/null && [ -f "$ps1" ] || return 1
  [ -f "$mark" ] && [ $(( $(date +%s) - $(mtime "$mark") )) -lt 40 ] && return 0   # already on its way
  touch "$mark"; mkdir -p "$PLAYER_QUEUE"
  # fully detached (setsid -f, all fds closed) so a caller reading our output, e.g. `setup | grep`, is not held open
  # and never inheriting fd 9: `say` starts the player while it holds the speech lock on fd 9, and a long-running
  # player that kept it would hold the lock forever (every later reply then waited 3 minutes)
  (cd /mnt/c && setsid -f powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass \
     -File "$(wslpath -w "$ps1")" -Dir "$(wslpath -w "$VOICE_SHARED")") </dev/null >/dev/null 2>&1 9>&-
}
