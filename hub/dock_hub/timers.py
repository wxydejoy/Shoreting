"""看得见的倒计时（F9）：番茄钟与正计时的状态板。

companion 负责到点开口，这里只管「现在走到哪了」——snapshot 每次来问，
按 monotonic 现算剩余秒数，不存绝对时刻之外的任何东西。
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TimerBoard:
    """一个倒计时 + 一个正计时。倒计时到点由 companion 清掉。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ends_at: float | None = None
        self._total: int = 0
        self._label: str = "计时"
        self._stop_started: float | None = None
        self._stop_label: str = "正计时"

    def arm(self, seconds: int, label: str = "计时") -> None:
        with self._lock:
            self._ends_at = time.monotonic() + max(1, int(seconds))
            self._total = max(1, int(seconds))
            self._label = (label or "计时").strip() or "计时"

    def clear(self) -> None:
        with self._lock:
            self._ends_at = None
            self._total = 0

    def start_stopwatch(self, label: str = "正计时") -> None:
        with self._lock:
            self._stop_started = time.monotonic()
            self._stop_label = (label or "正计时").strip() or "正计时"

    def stop_stopwatch(self) -> int | None:
        """停表并返回计了多少秒；没在计就返回 None。"""
        with self._lock:
            if self._stop_started is None:
                return None
            elapsed = int(time.monotonic() - self._stop_started)
            self._stop_started = None
            return elapsed

    def snapshot(self) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            if self._ends_at is not None:
                left = max(0, int(round(self._ends_at - now)))
                return {
                    "mode": "timer",
                    "label": self._label,
                    "total_sec": self._total,
                    "seconds_left": left,
                }
            if self._stop_started is not None:
                return {
                    "mode": "stopwatch",
                    "label": self._stop_label,
                    "seconds": int(now - self._stop_started),
                }
            return None


def speak_stopwatch_result(seconds: int) -> str:
    """停表时她说的话：25 分钟以下说分钟，再长带小时。"""
    if seconds < 60:
        return f"记了 {seconds} 秒。"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"记了 {minutes} 分钟。"
    hours = minutes // 60
    rest = minutes % 60
    if rest == 0:
        return f"记了 {hours} 个小时。"
    return f"记了 {hours} 小时 {rest} 分钟。"
