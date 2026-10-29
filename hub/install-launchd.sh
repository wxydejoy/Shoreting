#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo "先在 hub/ 里 uv sync" >&2
  exit 1
fi
BIN="$ROOT/.venv/bin/python"
LABEL="cn.weiekko.dock-hub"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
cat > "$DEST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${BIN}</string>
    <string>-m</string>
    <string>dock_hub</string>
    <string>--no-tray</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>LimitLoadToSessionType</key>
  <array>
    <string>Aqua</string>
    <string>Background</string>
  </array>
  <key>StandardOutPath</key>
  <string>${ROOT}/dock-hub.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/dock-hub.log</string>
</dict>
</plist>
EOF
UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootout "user/${UID_NUM}/${LABEL}" 2>/dev/null || true
sleep 0.4
# Cursor 会话里 gui/ 经常 I/O error 5，优先 user/
if launchctl bootstrap "user/${UID_NUM}" "$DEST" 2>/dev/null; then
  DOMAIN="user/${UID_NUM}"
else
  launchctl bootstrap "gui/${UID_NUM}" "$DEST"
  DOMAIN="gui/${UID_NUM}"
fi
launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl kickstart -k "${DOMAIN}/${LABEL}"
echo "已安装 LaunchAgent：$DEST"
echo "日志：$ROOT/dock-hub.log"
