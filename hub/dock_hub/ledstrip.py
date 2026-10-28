"""WS2812 灯带（ESP32 HTTP）。固件不是完整 JSON 解析器：键必须带引号，body ≤383 字节。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from dock_hub.errors import HubError

_BODY_LIMIT = 380


def percent_to_raw(percent: int) -> int:
    percent = max(1, min(100, int(percent)))
    return max(1, min(255, round(percent * 255 / 100)))


def raw_to_percent(raw: int) -> int:
    raw = max(0, min(255, int(raw)))
    if raw <= 0:
        return 0
    return max(1, min(100, round(raw * 100 / 255)))


def _join(base_url: str, path: str) -> str:
    return str(base_url).rstrip("/") + path


def apply_config(base_url: str, fields: dict[str, Any], timeout_sec: float = 2.0) -> dict[str, Any]:
    body = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > _BODY_LIMIT:
        raise HubError("bad_request", "灯带配置过长")
    req = urllib.request.Request(
        _join(base_url, "/api/config"),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    return _read_json(req, timeout_sec)


def read_config(base_url: str, timeout_sec: float = 2.0) -> dict[str, Any]:
    req = urllib.request.Request(_join(base_url, "/api/config"), method="GET")
    payload = _read_json(req, timeout_sec)
    if not isinstance(payload, dict):
        raise HubError("offline", "灯带返回无效")
    return payload


def _read_json(req: urllib.request.Request, timeout_sec: float) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise HubError("offline", f"灯带 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise HubError("offline", f"灯带离线：{exc.reason}") from exc
    except TimeoutError as exc:
        raise HubError("offline", "灯带超时") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HubError("offline", "灯带返回无效") from exc
    if not isinstance(payload, dict):
        raise HubError("offline", "灯带返回无效")
    return payload
