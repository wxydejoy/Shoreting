"""把控制台的 /api/hub/* 转给本机 Hub。

控制台（17880）和 Hub（17890）各占一个端口，页面直接 fetch Hub 就是跨源；
Hub 的 Token 也不该进浏览器。这里做一层同源转发：请求由控制台进程从
127.0.0.1 发出，Hub 按「本机」放行，前端只管打 /api/hub/<path>。

转发只搬字节，不解析语义：JSON 原样过，wav、整本书的上传也原样过。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

HUB_BASE = "http://127.0.0.1:17890"
TIMEOUT = 30.0
MAX_BODY = 32 << 20

_JSON = "application/json; charset=utf-8"

# 本机转发，绝不能走系统代理：机器上开着 HTTP_PROXY 时，urllib 默认会把
# 127.0.0.1:17890 也塞给代理，代理回一句 502 文本，页面上就全读不出来了。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_body(handler: Any, limit: int = MAX_BODY) -> bytes:
    """读请求体。超限抛 ValueError，让调用方回 413。"""
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except (TypeError, ValueError) as exc:
        raise ValueError("Content-Length 不对") from exc
    if length < 0 or length > limit:
        raise ValueError("请求太大")
    if not length:
        return b""
    return handler.rfile.read(length)


def forward(
    method: str,
    path: str,
    query: str = "",
    content_type: str = "",
    accept: str = "",
    body: bytes = b"",
    extra: dict[str, str] | None = None,
) -> tuple[int, str, bytes]:
    """发一次请求，回 (状态码, Content-Type, 字节)。连不上就给 502 JSON。

    extra 是少数要原样带给 Hub 的头，目前只有上传整本书用的 X-Filename。
    """
    url = f"{HUB_BASE}{path}"
    if query:
        url = f"{url}?{query}"
    headers = {"Accept": accept or "application/json"}
    if body:
        headers["Content-Type"] = content_type or "application/json"
    if extra:
        headers.update(extra)
    request = urllib.request.Request(url, data=body or None, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            return (
                response.status,
                response.headers.get("Content-Type") or _JSON,
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        try:
            return (
                exc.code,
                exc.headers.get("Content-Type") or _JSON,
                exc.read(),
            )
        finally:
            exc.close()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        message = json.dumps(
            {"ok": False, "message": f"Hub 没接上（{exc}）。先在服务里把它启动起来。"},
            ensure_ascii=False,
        )
        return 502, _JSON, message.encode("utf-8")
