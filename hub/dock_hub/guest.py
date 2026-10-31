"""访客模式（F26）：有外人的时候，一句话把她知道的事收起来。

features.md F26 的底线写在最前面：**只做「收起来」，不做「认出是谁」。**
人脸库那条路（摄像头存脸、比对身份）触碰 blueprint 的隐私红线，这里不走。
开关由主人一句话控制，够用、可控、随时可逆：

- 开：「有客人」「开访客模式」→ /chats /diary /week /memory 连页面带接口
  一律 404（对访客来说这些页「不存在」，比「无权访问」少一句追问）；
- 落盘一起停：跟客人的对话不写 jsonl、闲聊里不再捞事实（F28）——客人说
  「记住我姓什么」她也只应一句「客人在这儿，先不记这个」；
- 关：「客人走了」→ 一切照旧；
- 兜底：到点自动关（默认 6 小时，可配）。忘了说「客人走了」，也不会一直藏着。

状态只在内存里：Hub 重启本来就等于清场，不需要把「家里有客人」持久化。
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

DEFAULT_HOURS = 6.0
MIN_HOURS = 0.5
MAX_HOURS = 24.0

_ON_WORDS = ("有客人", "来客人", "客人来了", "来了客人", "来客人了")
_OFF_WORDS = ("客人走了", "没客人了", "送走客人")
_GUEST_RE = re.compile(r"访客模式")


def _tidy(text: str) -> str:
    return re.sub(r"[\s，。！？!?,.、]", "", text or "")


def parse_guest(text: str) -> str | None:
    """「有客人」→ "on"，「客人走了」→ "off"。不是开关访客模式的话返回 None。

    「访客模式」四个字只有整句就是它、或前面带开关动词才算，避免
    「把访客模式的事忘了」这类闲聊误触发。关的判断放在开的前面：
    「关访客模式」也包含「访客模式」。问句和否定句不是命令——
    「有客人吗」「家里没有客人」是聊天，别抢。
    """
    heard = _tidy(text)
    if not heard:
        return None
    if _GUEST_RE.search(heard):
        if re.search(r"关|退|结束|取消|解除", heard):
            return "off"
        if re.search(r"^(开|打开|进|启用|进入|开启|启动)|^(访客模式)$", heard) or re.search(
            r"^(请|麻烦|帮我|小岸)?(开|打开|进|启用|进入|开启|启动)", heard
        ):
            return "on"
        return None
    if re.search(r"(吗|呢)$|来过|有没有|是不是|什么", heard):
        return None
    if re.search(r"没有客人|没有来客人|不是客人", heard):
        return None
    if any(word in heard for word in _OFF_WORDS):
        return "off"
    if any(word in heard for word in _ON_WORDS):
        return "on"
    return None


def clamp_hours(raw: Any) -> float:
    """把配置/语音里的小时数夹到 [0.5, 24]，坏了回默认值。"""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HOURS
    if value != value:  # NaN
        return DEFAULT_HOURS
    return min(MAX_HOURS, max(MIN_HOURS, value))


class GuestMode:
    """访客模式的开关与到点自动恢复。线程安全（对话线程 / 请求线程都碰）。"""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._since: datetime | None = None
        self._until: datetime | None = None
        self._hours: float = DEFAULT_HOURS
        self._now = now or (lambda: datetime.now().astimezone())

    # ---- 状态 ------------------------------------------------------------

    def state(self) -> dict[str, Any] | None:
        """开着返回 {"since","until","hours"}；关着返回 None。顺带做过期检查。"""
        with self._lock:
            self._expire_locked()
            if self._since is None:
                return None
            return {
                "since": self._since.isoformat(timespec="seconds"),
                "until": self._until.isoformat(timespec="seconds") if self._until else None,
                "hours": self._hours,
            }

    def hiding(self) -> bool:
        """路由和落盘都问这一句：现在该不该把记录藏起来。"""
        with self._lock:
            self._expire_locked()
            return self._since is not None

    # ---- 开关 ------------------------------------------------------------

    def activate(self, hours: float = DEFAULT_HOURS) -> dict[str, Any]:
        """开。重复开就顺延截止时间——「又有客人来了」不该把计时清零重算。"""
        hours = clamp_hours(hours)
        now = self._now()
        with self._lock:
            self._hours = hours
            if self._since is None:
                self._since = now
            self._until = now + timedelta(hours=hours)
            return {
                "since": self._since.isoformat(timespec="seconds"),
                "until": self._until.isoformat(timespec="seconds"),
                "hours": self._hours,
            }

    def deactivate(self) -> None:
        with self._lock:
            self._since = None
            self._until = None

    # ---- 内部 ------------------------------------------------------------

    def _expire_locked(self) -> None:
        if self._since is None or self._until is None:
            return
        if self._now() >= self._until:
            self._since = None
            self._until = None
