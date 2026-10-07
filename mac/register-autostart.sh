#!/usr/bin/env bash
# macOS: start the claude-voice listener at login with launchd (the counterpart of windows/register-autostart.ps1).
# Usage: mac/register-autostart.sh        (install and start)   |   mac/register-autostart.sh --remove
# launchd runs the listener in the foreground and restarts it if it exits; logs go to ~/.claude/voice/listen.log.
set -euo pipefail
LABEL="com.claude-voice.listener"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.claude/voice"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$HOME/.local/bin/listen</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.claude/voice/listen.log</string>
  <key>StandardErrorPath</key><string>$HOME/.claude/voice/listen.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "registered $LABEL: the listener starts at login (and now). The first start asks for microphone access."
