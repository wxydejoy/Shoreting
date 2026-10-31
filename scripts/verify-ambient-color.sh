#!/usr/bin/env bash
# F17 变色半包的两分钟真机验证：灯带固件认不认 POST color，一条命令见分晓。
#
# 背景：变色半包搁在 shelve/ambient-color（灯带固件的 POST /api/config 带 color
# 字段没在真机上验过）。这个脚本不依赖 hub 代码——直接怼固件的 /api/config：
#
#   scripts/verify-ambient-color.sh http://192.168.31.23
#
# 步骤：读原样 → 写紫 → 读回 → 写暖橙（眼睛确认）→ 恢复原样。
# 全 PASS 就可以「git merge shelve/ambient-color」捞回来；哪步 FAIL 灯带固件就还不认。
set -euo pipefail

BASE="${1:?用法: verify-ambient-color.sh http://灯带IP}"
BASE="${BASE%/}"
CURL="curl -s -o /dev/null -w %{http_code}"

fail() { echo "FAIL: $1"; exit 1; }

echo "== 0/4 读原样 =="
ORIG=$(curl -sf "$BASE/api/config") || fail "GET /api/config 不通——先确认灯带 IP 和网络"
echo "$ORIG"
ORIG_COLOR=$(echo "$ORIG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('color',''))" 2>/dev/null || true)
ORIG_RAW=$(echo "$ORIG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('brightness',''))" 2>/dev/null || true)

restore() {
  echo "== 恢复原样 =="
  python3 - "$BASE" "$ORIG" <<'PY'
import json, sys, urllib.request
base, orig = sys.argv[1], json.loads(sys.argv[2])
keep = {k: v for k, v in orig.items() if k in ("color", "brightness", "on")}
req = urllib.request.Request(f"{base}/api/config", data=json.dumps(keep).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
urllib.request.urlopen(req, timeout=3)
print("已恢复:", keep)
PY
}
trap restore EXIT

echo "== 1/4 POST color=#7A5CFF（紫）=="
CODE=$($CURL -X POST -H "Content-Type: application/json" -d '{"color":"#7A5CFF"}' "$BASE/api/config") \
  || fail "POST 直接失败"
[ "$CODE" = "200" ] || fail "POST 返回 $CODE（固件可能不认 color 字段）"

echo "== 2/4 读回颜色 =="
BACK=$(curl -sf "$BASE/api/config") || fail "读回失败"
GOT=$(echo "$BACK" | python3 -c "import sys,json;print(json.load(sys.stdin).get('color',''))")
echo "读回 color=$GOT"
[ -n "$GOT" ] || fail "固件不回 color 字段——GET 能通但状态里没有，等于没记住"

echo "== 3/4 POST color=#FFD9A0（暖橙）=="
CODE=$($CURL -X POST -H "Content-Type: application/json" -d '{"color":"#FFD9A0"}' "$BASE/api/config")
[ "$CODE" = "200" ] || fail "第二个颜色 POST 返回 $CODE"

echo "== 4/4 眼睛确认 =="
echo "灯带现在应该是暖橙色。是 → 全部通过；不是 → 固件 200 但没执行，当 FAIL 处理。"
echo
echo "PASS: 固件认 POST color（原 color=${ORIG_COLOR:-无}，原 brightness=${ORIG_RAW:-无}，已排队恢复）"
echo "下一步: git merge shelve/ambient-color 把 F17 变色半包捞回主线。"
