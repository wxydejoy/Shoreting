#!/usr/bin/env bash
# 让守岸人在 Mini 喇叭说一句。Hub token 来自 ~/.hermes/.env，不要写进 git。
set -euo pipefail
TEXT="${1:-}"
if [[ -z "${TEXT}" && ! -t 0 ]]; then
  TEXT="$(cat)"
fi
TEXT="${TEXT#"${TEXT%%[![:space:]]*}"}"
TEXT="${TEXT%"${TEXT##*[![:space:]]}"}"
if [[ -z "${TEXT}" ]]; then
  echo "announce.sh: empty text" >&2
  exit 1
fi

ENV_FILE="${HERMES_HOME:-$HOME/.hermes}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

URL="${DOCK_HUB_URL:-http://10.83.22.31:17890}"
TOKEN="${DOCK_HUB_TOKEN:-}"
if [[ -z "${TOKEN}" ]]; then
  echo "announce.sh: DOCK_HUB_TOKEN unset, skip" >&2
  exit 0
fi

python3 - "$URL" "$TOKEN" "$TEXT" <<'PY'
import json, sys, urllib.error, urllib.request
url, token, text = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(
    url.rstrip("/") + "/v1/companion/announce",
    data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
    method="POST",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
)
try:
    with urllib.request.urlopen(req, timeout=8) as resp:
        sys.exit(0 if 200 <= resp.status < 300 else 1)
except (urllib.error.URLError, TimeoutError, OSError) as exc:
    print(f"announce.sh: {exc}", file=sys.stderr)
    sys.exit(1)
PY
