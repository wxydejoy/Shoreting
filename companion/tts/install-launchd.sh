#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo "先在 companion/tts 里 uv sync" >&2
  exit 1
fi
BIN="$ROOT/.venv/bin/python"
REF="$ROOT/voices/shorekeeper/clone.wav"
if [[ ! -f "$REF" ]]; then
  echo "缺少克隆参考音频：$REF" >&2
  exit 1
fi
LABEL="cn.weiekko.companion-tts"
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
    <string>${ROOT}/server.py</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>18100</string>
    <string>--model</string>
    <string>${ROOT}/models/Qwen3-TTS-12Hz-1.7B-Base-8bit</string>
    <string>--ref-audio</string>
    <string>${REF}</string>
    <string>--voice</string>
    <string>shorekeeper</string>
    <string>--play</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>60</integer>
  <key>LimitLoadToSessionType</key>
  <array>
    <string>Aqua</string>
    <string>Background</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HF_HOME</key>
    <string>${ROOT}/.cache-1.7b-8bit</string>
    <key>HF_HUB_OFFLINE</key>
    <string>1</string>
    <key>HF_HUB_DISABLE_XET</key>
    <string>1</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/tts.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/tts.log</string>
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
echo "日志：$ROOT/tts.log"
