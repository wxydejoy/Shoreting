"""Localhost HTTP UI for the service controller."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ASSET = Path(__file__).resolve().parent / "assets" / "index.html"
_SERVICE_ACTION = re.compile(r"/api/services/([a-z0-9-]+)/(start|stop|restart)")
_ALL_ACTION = re.compile(r"/api/all/(start|stop|restart)")
_WINDOWS = "/api/config/windows"


class Handler(BaseHTTPRequestHandler):
    controller = None

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            try:
                body = ASSET.read_bytes()
            except OSError as exc:
                self._json(500, {"ok": False, "message": f"页面打不开：{exc}"})
                return
            self._bytes(200, "text/html; charset=utf-8", body)
            return
        if path == "/api/status":
            self._json(200, {"services": self.controller.snapshot()})
            return
        self._json(404, {"ok": False, "message": "没有这个地址"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        service = _SERVICE_ACTION.fullmatch(path)
        if service:
            try:
                result = self.controller.apply(service.group(1), service.group(2))
            except KeyError:
                self._json(404, {"ok": False, "message": "没有这个服务"})
                return
            self._json(200, result)
            return
        whole = _ALL_ACTION.fullmatch(path)
        if whole:
            self._json(200, self.controller.apply_all(whole.group(1)))
            return
        if path == _WINDOWS:
            try:
                payload = self._json_body()
            except ValueError as exc:
                self._json(400, {"ok": False, "message": str(exc)})
                return
            self._json(200, self.controller.set_windows_address(str(payload.get("address") or "")))
            return
        self._json(404, {"ok": False, "message": "没有这个地址"})

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > 8192:
            raise ValueError("请求太大")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode())
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求不是 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求不是对象")
        return data

    def log_message(self, fmt: str, *args) -> None:
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self._bytes(code, "application/json; charset=utf-8", body)

    def _bytes(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(controller, host: str = "127.0.0.1", port: int = 17880) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"controller": controller})
    return ThreadingHTTPServer((host, port), handler)
