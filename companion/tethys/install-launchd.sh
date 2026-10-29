#!/usr/bin/env bash
# Mini：在现有 WeChat Hermes gateway 上打开 API :8642，并安装微信投递 :8643。
# 不要再起第二份 gateway（iLink token 同时只能一份）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/hermes ]]; then
  echo "创建 venv 并安装 hermes-agent…"
  uv venv
  uv pip install hermes-agent
fi
uv pip install ddgs || uv pip install ddgs -i https://pypi.tuna.tsinghua.edu.cn/simple
HERMES="$ROOT/.venv/bin/hermes"
PYTHON="$ROOT/.venv/bin/python"
export PATH="$ROOT/.venv/bin:$PATH"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_HOME"

ENV_FILE="$HERMES_HOME/.env"
TETHYS_ENV="$HERMES_HOME/tethys.env"

upsert_env() {
  local file="$1" key="$2" value="$3"
  python3 - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import sys
path, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
out, seen = [], False
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        name = stripped.split("=", 1)[0].strip()
        if name == key:
            out.append(f"{key}={value}")
            seen = True
            continue
    out.append(line)
if not seen:
    if out and out[-1] != "":
        out.append("")
    out.append(f"{key}={value}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

read_env() {
  local file="$1" key="$2"
  python3 - "$file" "$key" <<'PY'
from pathlib import Path
import sys
path, key = Path(sys.argv[1]), sys.argv[2]
if not path.is_file():
    raise SystemExit
for line in path.read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    name, value = stripped.split("=", 1)
    if name.strip() == key:
        print(value.strip().strip('"').strip("'"))
        break
PY
}

KEY="$(read_env "$ENV_FILE" API_SERVER_KEY || true)"
if [[ -z "${KEY}" ]]; then
  KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  echo "已生成 API_SERVER_KEY，写入 ${ENV_FILE} 与 ${TETHYS_ENV}"
fi

upsert_env "$ENV_FILE" API_SERVER_ENABLED true
upsert_env "$ENV_FILE" API_SERVER_HOST 0.0.0.0
upsert_env "$ENV_FILE" API_SERVER_PORT 8642
upsert_env "$ENV_FILE" API_SERVER_KEY "$KEY"
if ! grep -q '^DOCK_HUB_URL=' "$ENV_FILE" 2>/dev/null; then
  upsert_env "$ENV_FILE" DOCK_HUB_URL "http://10.83.22.31:17890"
fi
if ! grep -q '^DOCK_HUB_TOKEN=' "$ENV_FILE" 2>/dev/null; then
  echo "" >> "$ENV_FILE"
  echo "# 微信→书桌出声：填 Windows hub.yaml 的 token" >> "$ENV_FILE"
  echo "# DOCK_HUB_TOKEN=" >> "$ENV_FILE"
fi

cat > "$TETHYS_ENV" <<EOF
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=8642
API_SERVER_KEY=${KEY}
EOF
cp "$TETHYS_ENV" "$ROOT/tethys.env"

GW_PLIST="$HOME/Library/LaunchAgents/ai.hermes.gateway.plist"
if [[ -f "$GW_PLIST" ]]; then
  python3 - "$GW_PLIST" "$KEY" <<'PY'
import plistlib
import sys
from pathlib import Path
path, key = Path(sys.argv[1]), sys.argv[2]
data = plistlib.loads(path.read_bytes())
env = data.setdefault("EnvironmentVariables", {})
env["API_SERVER_ENABLED"] = "true"
env["API_SERVER_HOST"] = "0.0.0.0"
env["API_SERVER_PORT"] = "8642"
env["API_SERVER_KEY"] = key
path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
PY
fi

mkdir -p "$HERMES_HOME/skills"
for skill in shorekeeper-announce play-mini-music; do
  rm -rf "$HERMES_HOME/skills/$skill"
  cp -R "$ROOT/skills/$skill" "$HERMES_HOME/skills/$skill"
  chmod +x "$HERMES_HOME/skills/$skill/scripts/"*.sh
done
CONFIG_YAML="$HERMES_HOME/config.yaml"
if [[ ! -f "$CONFIG_YAML" ]] || ! grep -q '^web:' "$CONFIG_YAML"; then
  mkdir -p "$HERMES_HOME"
  printf '\nweb:\n  backend: ddgs\n  search_backend: ddgs\n' >> "$CONFIG_YAML"
fi

NOTIFY_LABEL="cn.weiekko.tethys-notify"
NOTIFY_PLIST="$HOME/Library/LaunchAgents/${NOTIFY_LABEL}.plist"
cat > "$NOTIFY_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${NOTIFY_LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT}/notify.py</string>
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
    <key>HERMES_HOME</key>
    <string>${HERMES_HOME}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/tethys-notify.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/tethys-notify.log</string>
</dict>
</plist>
EOF

UID_NUM="$(id -u)"

# 旧脚本会装第二份 gateway；若还在，卸掉以免抢微信 token
OLD_LABEL="cn.weiekko.tethys"
launchctl bootout "gui/${UID_NUM}/${OLD_LABEL}" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/${OLD_LABEL}.plist"

launchctl bootout "gui/${UID_NUM}/${NOTIFY_LABEL}" 2>/dev/null || true
launchctl bootout "user/${UID_NUM}/${NOTIFY_LABEL}" 2>/dev/null || true
sleep 0.3
if launchctl bootstrap "gui/${UID_NUM}" "$NOTIFY_PLIST" 2>/dev/null; then
  NOTIFY_DOMAIN="gui/${UID_NUM}"
else
  launchctl bootstrap "user/${UID_NUM}" "$NOTIFY_PLIST"
  NOTIFY_DOMAIN="user/${UID_NUM}"
fi
launchctl enable "${NOTIFY_DOMAIN}/${NOTIFY_LABEL}" 2>/dev/null || true
launchctl kickstart -k "${NOTIFY_DOMAIN}/${NOTIFY_LABEL}"

GW_LABEL="ai.hermes.gateway"
if launchctl print "user/${UID_NUM}/${GW_LABEL}" >/dev/null 2>&1; then
  echo "重启现有 Hermes gateway（user 域，打开 :8642 API）…"
  launchctl kickstart -k "user/${UID_NUM}/${GW_LABEL}"
elif launchctl print "gui/${UID_NUM}/${GW_LABEL}" >/dev/null 2>&1; then
  echo "重启现有 Hermes gateway（gui 域，打开 :8642 API）…"
  launchctl kickstart -k "gui/${UID_NUM}/${GW_LABEL}"
else
  echo "未找到 ${GW_LABEL}。请先 hermes gateway setup / 微信扫码，再重新运行本脚本。"
fi

echo ""
echo "探活:"
echo "  curl -s http://127.0.0.1:8642/health"
echo "  curl -s -H \"Authorization: Bearer \$(grep ^API_SERVER_KEY= ${TETHYS_ENV} | cut -d= -f2-)\" http://127.0.0.1:8642/v1/models"
echo "  curl -s http://127.0.0.1:8643/health"
echo ""
echo "Windows hub.yaml 片段，api_key 见 ${TETHYS_ENV}:"
cat <<YAML
companion:
  tethys:
    enabled: true
    base_url: "http://10.83.22.121:8642/v1"
    model: hermes-agent
    api_key: "${KEY}"
    timeout_sec: 120
    polish: true
    speak: true
    ack: "好。"
    weixin_notify: true
    session_key: tethys:wanderer
YAML
echo ""
echo "微信到书桌出声: 把 Windows Hub token 写进 ${ENV_FILE} 的 DOCK_HUB_TOKEN"
