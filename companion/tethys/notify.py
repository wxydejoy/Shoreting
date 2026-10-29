#!/usr/bin/env python3
"""Hub 把泰缇斯全文 POST 到这里，再 hermes send 到微信 home channel。

与 WeChat gateway 同机、不同端口（默认 8643），避免第二份 gateway 抢 iLink token。
鉴权与 Hermes API 同一把 API_SERVER_KEY。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HERMES = ROOT / ".venv" / "bin" / "hermes"
HOST = os.environ.get("TETHYS_NOTIFY_HOST", "0.0.0.0")
PORT = int(os.environ.get("TETHYS_NOTIFY_PORT") or 8643)


def _load_dotenv() -> None:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    env_path = home / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _api_key() -> str:
    return (os.environ.get("API_SERVER_KEY") or "").strip()


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    expected = _api_key()
    if not expected:
        return False
    header = handler.headers.get("Authorization") or ""
    return header.strip() == f"Bearer {expected}"


def _json(handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _send_weixin(text: str) -> dict:
    if not HERMES.is_file():
        return {"ok": False, "error": f"找不到 hermes：{HERMES}"}
    proc = subprocess.run(
        [str(HERMES), "send", "--to", "weixin", "--quiet"],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=30,
        cwd=str(ROOT),
        env={**os.environ, "PATH": f"{HERMES.parent}:{os.environ.get('PATH', '')}"},
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")[:400]
        return {"ok": False, "error": err or f"exit {proc.returncode}"}
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/health", "/v1/health"}:
            _json(self, 200, {"ok": True, "service": "tethys-notify"})
            return
        _json(self, 404, {"error": {"code": "not_found", "message": "未知接口"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/v1/weixin":
            _json(self, 404, {"error": {"code": "not_found", "message": "未知接口"}})
            return
        if not _authorized(self):
            _json(self, 401, {"error": {"code": "unauthorized", "message": "Token 不正确"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 200_000:
            _json(self, 400, {"error": {"code": "bad_request", "message": "text 不能为空"}})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json(self, 400, {"error": {"code": "bad_request", "message": "JSON 无效"}})
            return
        if not isinstance(payload, dict):
            _json(self, 400, {"error": {"code": "bad_request", "message": "JSON 必须是对象"}})
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            _json(self, 400, {"error": {"code": "bad_request", "message": "text 不能为空"}})
            return
        result = _send_weixin(text)
        if not result.get("ok"):
            _json(
                self,
                502,
                {"error": {"code": "notify_error", "message": result.get("error") or "微信发送失败"}},
            )
            return
        _json(self, 200, {"ok": True})


def main() -> int:
    _load_dotenv()
    if not _api_key():
        print("缺少 API_SERVER_KEY（写在 ~/.hermes/.env）", file=sys.stderr)
        return 1
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"tethys-notify http://{HOST}:{PORT}/v1/weixin", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
