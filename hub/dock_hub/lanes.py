"""慢请求单独一条道：名额满了就说 503，而不是无限开线程。

Hub 是 `ThreadingHTTPServer`，一个请求一个线程。快请求（snapshot、health）几十毫秒，
慢请求不一样：`chat` 要等模型把整句话写完，最多 90 秒；`read` 每块都要等 TTS 合成。
手机每 3 秒还在轮询 snapshot，如果对话能无限并发，线程数会跟着对话数一起涨，
真正要紧的轮询反而被挤在后面。

所以给这两条慢道各留固定几个位子。位子满了不排队——排队会让安卓那边一直转圈，
用户更不知道该干嘛；直接回 503 `busy` 加 `Retry-After`，手机上可以立刻说
「她还在这句上」。

名额是进程内的，和限流（`ratelimit.py`）不是一回事：限流管「你这个 IP 一分钟能问几次」，
这里管「此刻同时有几个在跑」。两个都要，因为它们防的东西不同。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from dock_hub.errors import HubError

BUSY_RETRY_AFTER_SEC = 2


class Lanes:
    """按名字分道，每条道最多 `slots` 个同时在跑；`slots <= 0` 表示这条路不限。"""

    def __init__(self, slots: dict[str, int] | None = None) -> None:
        self.slots = {name: max(0, int(count)) for name, count in (slots or {}).items()}
        self._gates = {
            name: threading.BoundedSemaphore(count)
            for name, count in self.slots.items()
            if count > 0
        }
        self._depth = dict.fromkeys(self._gates, 0)
        self._lock = threading.Lock()
        self._rejected = 0

    @classmethod
    def from_config(cls, limits: Any) -> "Lanes":
        return cls(
            {
                "chat": getattr(limits, "chat_lanes", 0),
                "read": getattr(limits, "read_lanes", 0),
            }
        )

    @contextmanager
    def hold(self, lane: str) -> Iterator[None]:
        gate = self._gates.get(lane)
        if gate is None:
            yield
            return
        if not gate.acquire(blocking=False):
            with self._lock:
                self._rejected += 1
            raise HubError(
                "busy",
                "她还在这句上，稍等一下",
                headers={"Retry-After": str(BUSY_RETRY_AFTER_SEC)},
            )
        with self._lock:
            self._depth[lane] += 1
        try:
            yield
        finally:
            with self._lock:
                self._depth[lane] -= 1
            gate.release()

    def depth(self, lane: str) -> int:
        with self._lock:
            return self._depth.get(lane, 0)

    def rejected(self) -> int:
        """因为挤不下被挡回去几次——值不值得调大名额，看它就知道了。"""
        with self._lock:
            return self._rejected
