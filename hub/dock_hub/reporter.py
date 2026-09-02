"""把书桌状态定时报到 Mini，对话只读内存里的最新一份。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urljoin

from dock_hub.config import MiniLinkConfig


class DeskReporter:
    def __init__(self, config: MiniLinkConfig, payload: Callable[[], dict[str, Any]]) -> None:
        self.config = config
        self._payload = payload
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._desk: dict[str, Any] = {}

    def start(self) -> None:
        if not self.config.enabled or not (self.config.base_url or "").strip():
            return
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            self.remember({"windows": self._payload()})
        except Exception:
            pass
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="desk-report", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._desk)) if self._desk else {}

    def remember(self, desk: dict[str, Any]) -> None:
        with self._lock:
            self._desk = desk

    def _loop(self) -> None:
        interval = max(0.4, self.config.pc_ms / 1000.0)
        while not self._stop.wait(interval):
            try:
                self._tick()
            except Exception:
                continue

    def _tick(self) -> None:
        windows = self._payload()
        posted = post_telemetry(self.config, windows)
        if posted:
            self.remember(posted)
            return
        with self._lock:
            desk = dict(self._desk)
            desk["windows"] = windows
            self._desk = desk


def post_telemetry(config: MiniLinkConfig, windows: dict[str, Any]) -> dict[str, Any] | None:
    base = (config.base_url or "").rstrip("/")
    if not base:
        return None
    url = urljoin(base + "/", "v1/telemetry")
    raw = json.dumps(windows, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=0.4) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    desk = body.get("desk")
    return desk if isinstance(desk, dict) else None
