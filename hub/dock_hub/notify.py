"""对讲 / 留言 / 微信（F33 · F4 · F34）：往手机递话的一条通道，三个入口。

- 书桌 → 手机（F33 反向 + F4）：对她说「跟她说我晚点回」，手机本地通知响一下；
- 手机 → 书桌（F33 正向）：App 里打一句话走 `/v1/companion/announce`，喇叭出声；
- 微信 → 书桌（F34）：机器人转发一句到 `/v1/wechat`，她念出来。单向播报，
  不做双向对话（不做清单第 3 条：身份、群聊、撤回，收益极低）。

递给手机的东西放在内存队列里，随 snapshot 下发（`notify.items`），安卓拿
自增 id 去重——比做 ack 协议便宜，丢了也就丢一句「饭好了」。
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

MAX_ITEMS = 10
KEEP_HOURS = 24

# 「跟她说我晚点回」（无标点直连）、「告诉她我下楼了」、「帮我带个话，饭好了」、
# 「留言：记得取快递」
_RELAY_RE = re.compile(
    r"^(?:请|麻烦|帮我)?"
    r"(?:跟她说|跟他说|告诉她|告诉他|给你自己说|带个话|带话|捎话|留言)"
    r"[，,：:.\s]*(.+)$",
)
# 「跟她说今天几度」是拿她当助手用，不是递话：问句一律不算
_QUESTION_RE = re.compile(r"几|多少|什么|怎么|哪|吗|呢|？|\?")


def parse_relay(text: str) -> str | None:
    """对书桌说的一句里认出「往手机递话」。不是递话返回 None。"""
    heard = (text or "").strip()
    if not heard or len(heard) > 120:
        return None
    match = _RELAY_RE.match(heard)
    if not match:
        return None
    content = (match.group(1) or "").strip("。！？!?；;，, ")
    # 问句不是留言（「跟她说今天几度」是在用她），宁可漏递不可递错
    if not content or _QUESTION_RE.search(content):
        return None
    return content


class NotifyBoard:
    """待递到手机的话。内存态，重启即清——重连后没响的通知不值得补发。"""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._items: list[dict[str, Any]] = []
        self._now = now or (lambda: datetime.now().astimezone())

    def post(self, text: str, source: str = "desk", *, now: datetime | None = None) -> dict[str, Any]:
        """记一条。返回这条的完整形态（调用方可以拿去汇报）。"""
        content = (text or "").strip()[:200]
        if not content:
            raise ValueError("递话内容为空")
        now = now or self._now()
        with self._lock:
            self._seq += 1
            item = {
                "id": self._seq,
                "text": content,
                "source": source,
                "at": now.isoformat(timespec="seconds"),
            }
            self._items.append(item)
            self._prune(now)
            self._items = self._items[-MAX_ITEMS:]
            return dict(item)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "last_id": self._seq,
                "items": [dict(item) for item in self._items],
            }

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=KEEP_HOURS)
        self._items = [
            item
            for item in self._items
            if str(item.get("at") or "") >= cutoff.isoformat(timespec="seconds")
        ]


def wechat_say_text(text: str, sender: str | None) -> str:
    """微信来的那句她怎么念。人名掐到 12 字，正文掐到 120 字。"""
    who = (sender or "").strip()[:12]
    body = (text or "").strip()[:120]
    if not body:
        return ""
    return f"微信，{who}说：{body}。" if who else f"微信说：{body}。"
