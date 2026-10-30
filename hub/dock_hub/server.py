from __future__ import annotations

import hmac
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from dock_hub.books import MAX_BOOK_BYTES
from dock_hub.chat_log import asset_page_html, chats_page_html, read_page_html
from dock_hub.errors import HubError
from dock_hub.service import DockHub
from dock_hub.setup_ui import is_local_client

MAX_BODY = 64 * 1024
_STATIC = {
    "litera.min.css": "text/css; charset=utf-8",
    "shell.css": "text/css; charset=utf-8",
    "rare.css": "text/css; charset=utf-8",
    "rare.js": "text/javascript; charset=utf-8",
}


def lan_ips() -> list[str]:
    found: list[str] = []

    def add(ip: str) -> None:
        if (
            ip
            and ip not in found
            and not ip.startswith("127.")
            and not ip.startswith("169.254.")
        ):
            found.append(ip)

    # 先探测 10.83.22 网段（安卓在这边），再走默认路由，避免只打出 Wi-Fi 的 192.168
    for probe in ("10.83.22.1", "223.5.5.5", "8.8.8.8"):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.3)
            sock.connect((probe, 80))
            add(sock.getsockname()[0])
            sock.close()
        except OSError:
            continue
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    found.sort(key=lambda ip: (0 if ip.startswith("10.83.22.") else 1, ip))
    return found or ["127.0.0.1"]


def make_handler(hub: DockHub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            if os.environ.get("DOCK_HUB_ACCESS_LOG"):
                print(f"{self.address_string()} {fmt % args}")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path.startswith("/assets/"):
                    name = path.removeprefix("/assets/")
                    ctype = _STATIC.get(name)
                    if ctype is None:
                        raise HubError("not_found", "未知接口")
                    self._html(200, asset_page_html(name), ctype)
                    return
                if path.startswith("/setup"):
                    from dock_hub import setup_ui

                    if path == "/setup" and not self._chats_ok():
                        self._redirect("/chats?next=/setup")
                        return
                    setup_ui.ensure_local(self)
                    if setup_ui.handle_setup_get(hub, path, self):
                        return
                    raise HubError("not_found", "未知接口")
                if path == "/health":
                    self._json(200, hub.health())
                    return
                if path == "/chats/claim":
                    query = parse_qs(parsed.query)
                    ticket = unquote((query.get("ticket") or [""])[0])
                    nxt = hub.claim_login_ticket(ticket)
                    if not nxt:
                        raise HubError("unauthorized", "登录已过期，请从托盘重新打开")
                    self.send_response(302)
                    self.send_header("Location", nxt)
                    self.send_header(
                        "Set-Cookie",
                        "helm_chats="
                        + quote(hub.config.token, safe="")
                        + "; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000",
                    )
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if path == "/chats":
                    self._html(200, chats_page_html(), "text/html; charset=utf-8")
                    return
                if path == "/read":
                    self._html(200, read_page_html(), "text/html; charset=utf-8")
                    return
                if path == "/v1/companion/read/jobs":
                    if not self._chats_ok():
                        raise HubError("unauthorized", "Token 不正确")
                    self._json(200, hub.companion_read_jobs())
                    return
                if path == "/v1/companion/books" or path.startswith("/v1/companion/books/"):
                    self._story_ok()
                    self._json(200, self._books_get(path))
                    return
                if path == "/v1/companion/story":
                    self._story_ok()
                    self._json(200, hub.books.story())
                    return
                if path == "/v1/companion/chats":
                    if not self._chats_ok():
                        raise HubError("unauthorized", "Token 不正确")
                    query = parse_qs(parsed.query)
                    try:
                        limit = int((query.get("limit") or ["200"])[0] or 200)
                    except ValueError:
                        limit = 200
                    q = unquote((query.get("q") or [""])[0])
                    self._json(200, hub.companion_chats(limit=limit, query=q))
                    return
                if path == "/v1/snapshot":
                    self._require_auth()
                    hub.note_presence()
                    self._json(200, hub.snapshot())
                    return
                if path == "/v1/weather":
                    # 天气由 Hub 代取：手机可能连在只有局域网的 Wi-Fi 上
                    self._require_auth()
                    from dock_hub.weather import fetch_weather

                    query = parse_qs(parsed.query)
                    city = (query.get("city") or [""])[0].strip()
                    try:
                        self._json(200, fetch_weather(city))
                    except ValueError as exc:
                        raise HubError("bad_request", str(exc)) from exc
                    except Exception as exc:  # noqa: BLE001
                        raise HubError("upstream_error", f"天气获取失败：{exc}", status=502) from exc
                    return
                if path.startswith("/v1/companion/audio/"):
                    if not self._chats_ok():
                        raise HubError("unauthorized", "Token 不正确")
                    audio_id = unquote(path[len("/v1/companion/audio/") :])
                    query = parse_qs(parsed.query)
                    download = (query.get("download") or [""])[0] in {"1", "true", "yes"}
                    filename = f"shorekeeper-{audio_id}.wav" if download else None
                    self._bytes(200, hub.companion_audio(audio_id), "audio/wav", filename=filename)
                    return
                raise HubError("not_found", "未知接口")
            except HubError as exc:
                self._json(exc.status, exc.body())

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path.startswith("/setup"):
                    from dock_hub import setup_ui

                    setup_ui.ensure_local(self)
                    body = self._read_json()
                    if setup_ui.handle_setup_post(hub, path, body, self):
                        return
                    raise HubError("not_found", "未知接口")
                if path == "/v1/companion/chat":
                    self._require_auth()
                    hub.note_presence()
                    body = self._read_json()
                    self._json(200, hub.companion_chat(body))
                    return
                if path == "/chats/login":
                    body = self._read_json()
                    token = str(body.get("token") or "").strip()
                    expected = hub.config.token
                    if not token or not hmac.compare_digest(token, expected):
                        raise HubError("unauthorized", "Token 不正确")
                    data = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header(
                        "Set-Cookie",
                        "helm_chats="
                        + quote(token, safe="")
                        + "; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000",
                    )
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/v1/companion/stop":
                    if not self._chats_ok():
                        raise HubError("unauthorized", "Token 不正确")
                    self._read_json()
                    self._json(200, hub.companion_stop())
                    return
                if path == "/v1/companion/announce":
                    self._require_auth()
                    body = self._read_json()
                    self._json(200, hub.companion_announce(body))
                    return
                if path == "/v1/companion/read":
                    if not self._chats_ok():
                        raise HubError("unauthorized", "Token 不正确")
                    body = self._read_json()
                    self._json(200, hub.companion_read(body))
                    return
                if path == "/v1/companion/books":
                    self._story_ok()
                    from dock_hub.books import _log

                    name = unquote(self.headers.get("X-Filename") or "")
                    _log("book_upload", name=name or "-", bytes=self.headers.get("Content-Length", "0"))
                    try:
                        data = self._read_bytes(MAX_BOOK_BYTES)
                        self._json(200, hub.books.save_upload(name, data))
                    except HubError:
                        raise
                    except Exception as exc:
                        _log("book_upload_error", name=name or "-", error=type(exc).__name__)
                        raise HubError("bad_request", f"没放进书架：{exc}") from exc
                    return
                if path == "/v1/companion/story/timer":
                    self._story_ok()
                    body = self._read_json()
                    self._json(200, hub.books.set_timer(body.get("minutes")))
                    return
                books_prefix = "/v1/companion/books/"
                if path.startswith(books_prefix):
                    self._story_ok()
                    rest = [unquote(part) for part in path[len(books_prefix) :].split("/") if part]
                    body = self._read_json()
                    self._json(200, self._books_post(rest, body))
                    return
                jobs_prefix = "/v1/companion/read/jobs/"
                if path.startswith(jobs_prefix):
                    if not self._chats_ok():
                        raise HubError("unauthorized", "Token 不正确")
                    rest = path[len(jobs_prefix) :]
                    parts = [unquote(p) for p in rest.split("/") if p]
                    if len(parts) != 2:
                        raise HubError("not_found", "未知接口")
                    self._read_json()
                    self._json(200, hub.companion_read_job_action(parts[0], parts[1]))
                    return
                if path == "/v1/login-ticket":
                    self._require_auth()
                    body = self._read_json()
                    self._json(200, hub.mint_login_ticket(str(body.get("next") or "/setup")))
                    return
                if path == "/v1/telemetry":
                    self._require_auth()
                    body = self._read_json()
                    self._json(200, hub.ingest_telemetry(body))
                    return
                if path == "/v1/leave":
                    self._require_auth()
                    self._read_json()
                    self._json(200, hub.leave_home())
                    return
                scenes_prefix = "/v1/scenes/"
                if path.startswith(scenes_prefix):
                    scene_id = unquote(path[len(scenes_prefix) :])
                    if not scene_id or "/" in scene_id:
                        raise HubError("not_found", "未知接口")
                    self._require_auth()
                    self._read_json()
                    self._json(200, hub.run_scene(scene_id))
                    return
                prefix = "/v1/devices/"
                suffix = "/command"
                if not (path.startswith(prefix) and path.endswith(suffix)):
                    raise HubError("not_found", "未知接口")
                device_id = unquote(path[len(prefix) : -len(suffix)])
                if not device_id or "/" in device_id:
                    raise HubError("not_found", "未知接口")
                self._require_auth()
                body = self._read_json()
                self._json(200, hub.command(device_id, body))
            except HubError as exc:
                self._json(exc.status, exc.body())

        def _cookie(self, name: str) -> str:
            raw = self.headers.get("Cookie") or ""
            for part in raw.split(";"):
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                if key.strip() == name:
                    return unquote(value.strip())
            return ""

        def _chats_ok(self) -> bool:
            if is_local_client(self.client_address):
                return True
            expected = hub.config.token
            header = self.headers.get("Authorization") or self.headers.get("authorization") or ""
            bearer = header.strip()
            if bearer.lower().startswith("bearer "):
                got = bearer[7:].strip()
                if got and hmac.compare_digest(got, expected):
                    return True
            cookie = self._cookie("helm_chats")
            return bool(cookie) and hmac.compare_digest(cookie, expected)

        def _require_auth(self) -> None:
            header = self.headers.get("Authorization") or self.headers.get("authorization") or ""
            expected = f"Bearer {hub.config.token}"
            if header.strip() != expected:
                raise HubError("unauthorized", "Token 不正确")

        def _story_ok(self) -> None:
            if not self._chats_ok():
                raise HubError("unauthorized", "Token 不正确")
            if not hub.companion.config.enabled:
                raise HubError("not_found", "未开启桌面伴侣")

        def _books_get(self, path: str) -> dict[str, Any]:
            if path == "/v1/companion/books":
                return hub.books.list_books()
            rest = [unquote(part) for part in path[len("/v1/companion/books/") :].split("/") if part]
            if len(rest) == 1:
                return hub.books.open_book(rest[0])
            if len(rest) == 3 and rest[1] == "chapter":
                try:
                    index = int(rest[2])
                except ValueError as exc:
                    raise HubError("not_found", "没有这一章") from exc
                return hub.books.chapter(rest[0], index)
            raise HubError("not_found", "未知接口")

        def _books_post(self, rest: list[str], body: dict[str, Any]) -> dict[str, Any]:
            if len(rest) != 2:
                raise HubError("not_found", "未知接口")
            book_id, action = rest
            if action == "play":
                offset = body.get("offset")
                if offset is None:
                    return hub.books.play(book_id)
                return hub.books.play(book_id, int(offset))
            if action == "pause":
                return hub.books.pause()
            if action == "bookmark":
                return hub.books.bookmark(book_id, int(body.get("offset") or 0))
            raise HubError("not_found", "未知接口")

        def _read_bytes(self, limit: int) -> bytes:
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError as exc:
                raise HubError("bad_request", "请求体过大") from exc
            if length < 0 or length > limit:
                raise HubError("bad_request", "文件超过 32MB")
            return self.rfile.read(length) if length else b""

        def _read_json(self) -> dict[str, Any]:
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError as exc:
                raise HubError("bad_request", "JSON 无效") from exc
            if length < 0 or length > MAX_BODY:
                raise HubError("bad_request", "请求体过大")
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HubError("bad_request", "JSON 无效") from exc
            if not isinstance(data, dict):
                raise HubError("bad_request", "JSON 必须是对象")
            return data

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _html(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _redirect(self, location: str) -> None:
            data = b""
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _bytes(
            self,
            status: int,
            payload: bytes,
            content_type: str,
            filename: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "shorekeeper.wav"
                self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def serve(hub: DockHub, *, blocking: bool = True) -> ThreadingHTTPServer:
    handler = make_handler(hub)
    server = ThreadingHTTPServer((hub.config.host, hub.config.port), handler)
    ips = lan_ips()
    print(f"岸亭 Hub v1  http://{ips[0]}:{hub.config.port}", flush=True)
    for extra in ips[1:]:
        print(f"             http://{extra}:{hub.config.port}", flush=True)
    print(f"配置向导     http://127.0.0.1:{hub.config.port}/setup  （本机直接开；局域网要 Token）", flush=True)
    print(f"聊天记录     http://127.0.0.1:{hub.config.port}/chats  （本机直接开；局域网要 Token）", flush=True)
    print(f"朗读         http://127.0.0.1:{hub.config.port}/read   （本机直接开；局域网要 Token）", flush=True)
    if hub.config.path:
        print(f"配置         {hub.config.path}", flush=True)
    if hub.uses_probe():
        print(f"Windows 探针 {hub.config.probe.url}", flush=True)
        snap = hub.pc_snapshot() or {}
        hub.follow_pc(bool(snap.get("online")))
    else:
        if os.name == "nt":
            print("Windows 防火墙请放行入站 TCP 17890：", flush=True)
            print(
                '  netsh advfirewall firewall add rule name="Shoreting Hub" '
                "dir=in action=allow protocol=TCP localport=17890",
                flush=True,
            )
        hub.follow_pc(True)
    if not blocking:
        return server
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub 已停止")
        hub.follow_pc(False)
        server.server_close()
    return server
