#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/helm-mini && ! -x .venv/bin/python ]]; then
  echo "先在 mini/ 里 uv sync" >&2
  exit 1
fi
BIN="$ROOT/.venv/bin/python"
LABEL="cn.weiekko.helm-mini"
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
    <string>helm_mini</string>
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
  <key>EnvironmentVariables</key>
  <dict>
    <key>HELM_MINI_TOKEN</key>
    <string>helm-mini-weiekko</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/helm-mini.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/helm-mini.log</string>
</dict>
</plist>
EOF
UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootout "user/${UID_NUM}/${LABEL}" 2>/dev/null || true
sleep 0.4
if launchctl bootstrap "gui/${UID_NUM}" "$DEST" 2>/dev/null; then
  DOMAIN="gui/${UID_NUM}"
else
  launchctl bootstrap "user/${UID_NUM}" "$DEST"
  DOMAIN="user/${UID_NUM}"
fi
launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl kickstart -k "${DOMAIN}/${LABEL}"
echo "已安装 LaunchAgent：$DEST"
echo "日志：$ROOT/helm-mini.log"
