"""Talk to the Hub's feature API on localhost.

控制台本身不解析 yaml：开关的语义（合法范围、设备与场景的引用、写回之后的
热加载）都在 Hub 那边，这里只做一层本机转发。Hub 没起来的时候老老实实说
「先启动 Hub」，而不是自己猜一份配置出来。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

HUB_BASE = "http://127.0.0.1:17890"
TIMEOUT = 4.0

Transport = Callable[[str, str, dict[str, Any] | None], dict[str, Any]]

# 同 hub_proxy：本机请求不走系统代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def default_transport(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    *,
    base_url: str = HUB_BASE,
    timeout: float = TIMEOUT,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    open_url = opener or _OPENER.open
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, method=method, headers=headers
    )
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read(1 << 18).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(1 << 16).decode("utf-8", errors="replace")
        finally:
            exc.close()  # 4xx 的响应体也是连接，不关会被 ResourceWarning 抓到
        message = ""
        try:
            payload = json.loads(detail)
            if isinstance(payload, dict):
                message = str(payload.get("message") or "")
        except (ValueError, TypeError):
            message = detail[:200]
        return {"ok": False, "message": message or f"Hub 回了 {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "message": f"Hub 没接上（{exc}）。先在上面把它启动起来。"}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {"ok": False, "message": "Hub 回的不是 JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "message": "Hub 回的不是对象"}
    return payload


class FeaturesClient:
    def __init__(
        self,
        base_url: str = HUB_BASE,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url
        if transport is None:
            self._transport: Transport = lambda method, path, body: default_transport(
                method, path, body, base_url=base_url
            )
        else:
            self._transport = transport

    def catalog(self) -> dict[str, Any]:
        return self._transport("GET", "/setup/api/features", None)

    def save(self, key: str, value: Any) -> dict[str, Any]:
        return self._transport("POST", "/setup/api/features", {"key": key, "value": value})
