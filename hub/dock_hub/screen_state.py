"""k20 屏网关增补路由的运行时状态（协议增补提案）。

契约：dsh-shorekeeper 仓库 `plugins/dsh-shorekeeper-k20/docs/protocol-addendum.md`；
客户端实现：同仓库 `src/gateway.ts`。纯逻辑类——server.py 的三条新路由
（/v1/caption、/v1/phone/layout、/v1/lock）全部委托到这里，单测不依赖 DockHub 全家桶。

- caption：字幕文本 + 上屏时间戳（安卓按 `at` 去重）；clear 置回 None
- layout：布局覆盖（槽位 → 矩形 0–1 画布比例），rev 只增不减，安卓按 rev 变大才套用
- lock：熄屏请求时间戳（安卓边缘触发执行设备管理员锁屏；渲染/执行面待安卓侧）
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from dock_hub.errors import HubError

CAPTION_MAX_CHARS = 200
SCALE_MIN, SCALE_MAX = 60, 140
RECT_KEYS = ("x", "y", "w", "h")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ScreenState:
    """caption / layout / lock 三块屏上运行时状态（线程安全）。"""

    def __init__(self, base_layout_rev: int = 0, base_modules: dict[str, dict[str, Any]] | None = None) -> None:
        self._lock_guard = threading.Lock()
        self._caption: dict[str, Any] | None = None
        self._layout_rev = max(0, int(base_layout_rev))
        self._modules: dict[str, dict[str, Any]] = dict(base_modules or {})
        self._lock_requested_at: str | None = None

    # -- caption ---------------------------------------------------------

    def set_caption(self, body: dict[str, Any]) -> dict[str, bool]:
        """推字幕或清字幕。`{text, scale?}` 上屏；`{clear: true}` 清掉。"""
        with self._lock_guard:
            if body.get("clear") is True:
                self._caption = None
                return {"ok": True}
            text = str(body.get("text") or "").strip()
            if not text:
                raise HubError("bad_request", "text 与 clear 必须给一个")
            entry: dict[str, Any] = {"text": text[:CAPTION_MAX_CHARS], "at": _now()}
            scale = body.get("scale")
            if scale is not None:
                try:
                    scale_value = int(scale)  # type: ignore[arg-type]
                except (TypeError, ValueError) as exc:
                    raise HubError("bad_request", "scale 要 60–140 的整数") from exc
                if not SCALE_MIN <= scale_value <= SCALE_MAX:
                    raise HubError("bad_request", f"scale 要 {SCALE_MIN}–{SCALE_MAX}，收到 {scale_value}")
                entry["scale"] = scale_value
            self._caption = entry
            return {"ok": True}

    def caption_payload(self) -> dict[str, Any] | None:
        with self._lock_guard:
            return dict(self._caption) if self._caption else None

    # -- layout ----------------------------------------------------------

    def apply_layout(self, patch: Any) -> dict[str, bool]:
        """合并布局覆盖并递增 rev。patch 形如 {"modules": {槽位: 矩形}}。"""
        with self._lock_guard:
            if not isinstance(patch, dict) or not isinstance(patch.get("modules"), dict):
                raise HubError("bad_request", "layout.modules 必须是对象")
            for slot, rect in patch["modules"].items():
                name = str(slot)
                if not isinstance(rect, dict):
                    raise HubError("bad_request", f"layout.modules.{name} 必须是对象")
                for key in RECT_KEYS:
                    if key in rect:
                        value = rect[key]
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                            raise HubError("bad_request", f"layout.modules.{name}.{key} 要 0–1 的画布比例")
                if "visible" in rect and not isinstance(rect["visible"], bool):
                    raise HubError("bad_request", f"layout.modules.{name}.visible 要布尔值")
            for slot, rect in patch["modules"].items():
                merged = dict(self._modules.get(str(slot), {}))
                merged.update(rect)
                self._modules[str(slot)] = merged
            self._layout_rev += 1
            return {"ok": True}

    def layout_payload(self) -> dict[str, Any]:
        with self._lock_guard:
            return {"rev": self._layout_rev, "modules": {k: dict(v) for k, v in self._modules.items()}}

    def layout_rev(self) -> int:
        with self._lock_guard:
            return self._layout_rev

    # -- lock ------------------------------------------------------------

    def request_lock(self) -> dict[str, bool]:
        with self._lock_guard:
            self._lock_requested_at = _now()
            return {"ok": True}

    def lock_payload(self) -> dict[str, Any] | None:
        with self._lock_guard:
            return {"requested_at": self._lock_requested_at} if self._lock_requested_at else None
