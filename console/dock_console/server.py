"""Localhost HTTP UI for the service controller."""

from __future__ import annotations

import hmac
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dock_console import hub_proxy, phone_bg
from dock_console.features import FeaturesClient


ASSETS = Path(__file__).resolve().parent / "assets"
ASSET = ASSETS / "index.html"
_STATIC = {
    "/assets/console.js": ("console.js", "text/javascript; charset=utf-8"),
    "/assets/console.css": ("console.css", "text/css; charset=utf-8"),
}
_SERVICE_ACTION = re.compile(r"/api/services/([a-z0-9-]+)/(start|stop|restart)")
_ALL_ACTION = re.compile(r"/api/all/(start|stop|restart)")
_WINDOWS = "/api/config/windows"
_FEATURES = "/api/features"
_HUB = "/api/hub"
_HUB_PREFIX = _HUB + "/"
# 手机上的背景服务：控制台只做转发，逻辑全在 dock_console.phone_bg
_PHONE_BG = "/api/phone/backgrounds"
_PHONE_CLEAR = _PHONE_BG + "/clear"
_PHONE_LAYOUT = "/api/phone/layout"
_PHONE_SELECT = re.compile(r"/api/phone/backgrounds/([^/]+)/select")
_PHONE_THUMB = re.compile(r"/api/phone/backgrounds/([^/]+)/thumb")
_PHONE_ITEM = re.compile(r"/api/phone/backgrounds/([^/]+)")

# 本机回环地址：这些来源免 Token（沿用 Hub 的规矩）
_LOCAL_ADDRESSES = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

# 换版时这两个文件一起变；时间戳跟着走，URL 也就跟着变
_SHELL_ASSETS = ("console.js", "console.css")


def asset_stamp() -> int:
    """静态资源的「版本」：取最新的构建时间。

    index.html 里的 js/css 地址会带上它。固定文件名 + no-store 对大多数
    浏览器够用，但手机「添加到主屏幕」的 WebView、微信内置浏览器，还有
    链路上任何一层缓存，都可能把 /assets/console.js 留在本地 —— 那就成了
    「改了、也刷新了，看到还是旧页面」。URL 变了，谁也留不住。
    """
    latest = 0
    for name in _SHELL_ASSETS:
        try:
            latest = max(latest, int((ASSETS / name).stat().st_mtime))
        except OSError:
            continue
    return latest


def versioned_shell(html: str, stamp: int) -> str:
    if not stamp:
        return html
    for name in _SHELL_ASSETS:
        # 磁盘上的 index.html 可能已经被 stamp-index.mjs 写过一次版本号，
        # 这里整段换掉，别叠成 ?v=1?v=2
        html = re.sub(
            rf"/assets/{re.escape(name)}(\?v=\d+)?",
            f"/assets/{name}?v={stamp}",
            html,
        )
    return html


def check_access(
    client_ip: str,
    token: str | None,
    *,
    query_key: str = "",
    cookie: str = "",
    authorization: str = "",
) -> bool:
    """本机直接放行；局域网凭 ?key= / Cookie / Bearer 三选一。

    浏览器打不开带 Authorization 的请求，所以约定 ?key=<token> 打开页面，
    服务端顺手种 cookie，之后的 /api/* 同源请求自己就带上了。
    没配 Token 时局域网一律拒绝——宁可控制台从 Win 打不开，不能裸奔上 LAN。
    """
    if client_ip in _LOCAL_ADDRESSES:
        return True
    if not token:
        return False
    candidates = (
        query_key.strip(),
        _cookie_key(cookie),
        authorization.removeprefix("Bearer ").strip(),
    )
    return any(got and hmac.compare_digest(got, token) for got in candidates)


def _cookie_key(cookie: str) -> str:
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "key":
            return value.strip()
    return ""


def read_hub_token(path: Path) -> str:
    """hub.yaml 顶层的 token。控制台环境不保证有 PyYAML，按行找就够了。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for raw in text.splitlines():
        # 顶层的 token:（probe: 段里的 token 有缩进，不会误中）
        if raw.startswith("token:"):
            return raw.split(":", 1)[1].strip().strip("'\"")
    return ""


class Handler(BaseHTTPRequestHandler):
    controller = None
    features: FeaturesClient | None = None
    token: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            # 探活：launchd 控制台服务和局域网查「控制台活没活」都用它，免鉴权
            self._json(200, {"ok": True, "service": "dock-console"})
            return
        if not self._auth():
            return
        if path.startswith(_HUB_PREFIX):
            self._hub("GET")
            return
        if path in {"/", "/index.html", "/features", "/features/"}:
            # 单页应用：所有入口都发同一份壳，前端自己按 # 路由。
            # 带 ?key= 打开时顺手种 cookie，之后的 /api/* 同源请求自己带上。
            key = (parse_qs(urlparse(self.path).query).get("key") or [""])[0]
            self._shell(cookie=f"key={key}; Path=/; HttpOnly; SameSite=Lax" if key else None)
            return
        if path in _STATIC:
            self._asset(*self._static(path))
            return
        if path == "/api/status":
            self._json(200, {"services": self.controller.snapshot()})
            return
        if path == _FEATURES:
            self._json(200, self._client().catalog())
            return
        if path == _PHONE_BG:
            self._phone(phone_bg.list_backgrounds)
            return
        if path == _PHONE_LAYOUT:
            self._phone(phone_bg.get_layout)
            return
        preview = _PHONE_THUMB.fullmatch(path)
        if preview:
            ident = unquote(preview.group(1))
            self._phone_thumb(ident)
            return
        self._json(404, {"ok": False, "message": "没有这个地址"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth():
            return
        path = urlparse(self.path).path
        if path.startswith(_HUB_PREFIX):
            self._hub("POST")
            return
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
        if path == _FEATURES:
            try:
                payload = self._json_body()
            except ValueError as exc:
                self._json(400, {"ok": False, "message": str(exc)})
                return
            key = str(payload.get("key") or "").strip()
            if not key:
                self._json(400, {"ok": False, "message": "没说是哪个开关"})
                return
            self._json(200, self._client().save(key, payload.get("value")))
            return
        if path == _PHONE_BG:
            self._phone_upload()
            return
        if path == _PHONE_CLEAR:
            self._phone(phone_bg.clear)
            return
        picked = _PHONE_SELECT.fullmatch(path)
        if picked:
            ident = unquote(picked.group(1))
            self._phone(lambda: phone_bg.select(ident))
            return
        self._json(404, {"ok": False, "message": "没有这个地址"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._auth():
            return
        path = urlparse(self.path).path
        if path.startswith(_HUB_PREFIX):
            self._hub("DELETE")
            return
        removed = _PHONE_ITEM.fullmatch(path)
        if removed:
            ident = unquote(removed.group(1))
            self._phone(lambda: phone_bg.delete(ident))
            return
        self._json(404, {"ok": False, "message": "没有这个地址"})

    def _hub(self, method: str) -> None:
        """同源转发到 Hub。路径原样保留（含已编码的中文书名）。"""
        parsed = urlparse(self.path)
        target = parsed.path[len(_HUB) :]
        if ".." in target:
            self._json(400, {"ok": False, "message": "路径不对"})
            return
        try:
            body = hub_proxy.read_body(self) if method != "GET" else b""
        except ValueError as exc:
            code = 413 if "太大" in str(exc) else 400
            self._json(code, {"ok": False, "message": str(exc)})
            return
        # 上传整本书时 Hub 从 X-Filename 拿文件名，只有这个头需要原样带过去
        extra = {}
        filename = self.headers.get("X-Filename")
        if filename:
            extra["X-Filename"] = filename
        status, content_type, payload = hub_proxy.forward(
            method,
            target,
            parsed.query,
            self.headers.get("Content-Type") or "",
            self.headers.get("Accept") or "",
            body,
            extra,
        )
        self._bytes(status, content_type, payload)

    def _phone(self, call) -> None:
        """跑一次手机背景服务的调用，把 PhoneError 翻成一句人话。"""
        try:
            payload = call()
        except phone_bg.PhoneError as exc:
            self._json(502, {"ok": False, "message": str(exc)})
            return
        self._json(200, payload)

    def _phone_thumb(self, ident: str) -> None:
        """预览图回的是图片字节，不是 JSON。kind / size 走查询串做缓存键。"""
        query = parse_qs(urlparse(self.path).query)
        kind = (query.get("kind") or ["video"])[0]
        try:
            size = int((query.get("size") or ["0"])[0])
        except ValueError:
            size = 0
        try:
            data = phone_bg.thumbnail(ident, kind=kind, size=size)
        except phone_bg.PhoneError as exc:
            self._json(502, {"ok": False, "message": str(exc)})
            return
        self._bytes(200, data["content_type"], data["bytes"])

    def _phone_upload(self) -> None:
        """上传是流式转发：视频几十 MB，不能整段读进内存。"""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            self._json(400, {"ok": False, "message": "没有收到文件"})
            return
        if length > phone_bg.MAX_UPLOAD:
            # 请求体还挂在连接上没读完，这条连接不能再复用
            self.close_connection = True
            self._json(413, {"ok": False, "message": "视频不能超过 200MB"})
            return
        try:
            payload = phone_bg.upload(
                self.rfile.read, self.headers.get("Content-Type") or "", length
            )
        except phone_bg.PhoneError as exc:
            self.close_connection = True
            self._json(502, {"ok": False, "message": str(exc)})
            return
        self._json(201, payload)

    def _client(self) -> FeaturesClient:
        if self.features is None:
            self.features = FeaturesClient()
        return self.features

    def _auth(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        if check_access(
            self.client_address[0],
            type(self).token,
            query_key=(query.get("key") or [""])[0],
            cookie=self.headers.get("Cookie") or "",
            authorization=self.headers.get("Authorization") or "",
        ):
            return True
        self._json(
            401,
            {"ok": False, "message": "缺 Token：地址后面带 ?key=<hub.yaml 里的 token>，或从 Mac 本机打开"},
        )
        return False

    def _static(self, path: str) -> tuple[Path, str]:
        name, content_type = _STATIC[path]
        return ASSETS / name, content_type

    def _shell(self, cookie: str | None = None) -> None:
        """单页壳。js/css 地址带上构建时间戳，换版就不会被缓存留住。"""
        try:
            html = ASSET.read_text(encoding="utf-8")
        except OSError as exc:
            self._json(500, {"ok": False, "message": f"页面打不开：{exc}"})
            return
        body = versioned_shell(html, asset_stamp()).encode("utf-8")
        self._bytes(200, "text/html; charset=utf-8", body, cookie=cookie)

    def _asset(
        self, path: Path, content_type: str = "text/html; charset=utf-8", cookie: str | None = None
    ) -> None:
        try:
            body = path.read_bytes()
        except OSError as exc:
            self._json(500, {"ok": False, "message": f"页面打不开：{exc}"})
            return
        if cookie:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)
            return
        self._bytes(200, content_type, body)

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

    def _bytes(
        self, code: int, content_type: str, body: bytes, cookie: str | None = None
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)


def serve(
    controller,
    host: str = "127.0.0.1",
    port: int = 17880,
    features=None,
    token: str | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler",
        (Handler,),
        {"controller": controller, "features": features, "token": token},
    )
    return ThreadingHTTPServer((host, port), handler)
