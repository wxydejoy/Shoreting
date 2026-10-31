"""谁在敲门：按「来源 + 端点」算的令牌桶。

为什么需要它：`/v1/companion/chat` 每被调一次就是一次大模型调用，一次几千
token。家里网段里任何拿到 token 的东西 —— 手机、自己写的脚本、误装的 App ——
都能把额度刷干。token 泄露的时候，这层限流也是唯一还生效的止损点。

做法刻意简单：每个 (来源, 端点) 一个桶，容量就是「每分钟允许几次」，按同一
速率匀速回填，不攒积分。不引 Redis、不起清理线程 —— Hub 是单进程，一把锁够用。

认的是来源 IP 而不是 token：token 只有一枚，按 token 限等于全局限，一个人刷爆
全家都动不了；按 IP 分桶，至少别的设备还能用。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 只为类型检查
    from dock_hub.config import LimitsConfig

# 桶的记账本不能无限长：陌生 IP 扫端口时每个来源都会留一个桶。
MAX_KEYS = 1024
# 超过这么久没动静的桶直接丢掉，它已经回满，留着也只是占内存。
IDLE_SEC = 600.0


@dataclass
class _Bucket:
    tokens: float
    stamp: float


class Limiter:
    """一个端点的限流器。"""

    def __init__(self, per_minute: int, *, burst: int | None = None) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute 必须是正数")
        self.per_minute = per_minute
        # 默认满桶 = 一分钟的量，也就是允许「攒着一次用完」。
        self.capacity = float(burst if burst is not None else per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def take(self, key: str, now: float | None = None) -> float:
        """扣一次。返回 0 表示放行，否则是还要等几秒。

        `now` 只给测试传，真实调用走 time.monotonic()：墙上时间会被 NTP 调整，
        桶按它算会出现「时间倒流，于是永远有额度」这种事。
        """
        stamp_now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(stamp_now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, stamp=stamp_now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, stamp_now - bucket.stamp)
                bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.rate)
                bucket.stamp = stamp_now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return 0.0
            return max((1.0 - bucket.tokens) / self.rate, 0.1)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def _prune(self, now: float) -> None:
        if len(self._buckets) < MAX_KEYS:
            return
        for key, bucket in list(self._buckets.items()):
            if now - bucket.stamp > IDLE_SEC:
                del self._buckets[key]

    def __len__(self) -> int:
        return len(self._buckets)


class Gate:
    """一台 Hub 上所有端点的限流集合。没登记的端点 = 不限。"""

    def __init__(self, policies: dict[str, int]) -> None:
        self._limiters = {
            name: Limiter(per_minute) for name, per_minute in policies.items() if per_minute > 0
        }

    @classmethod
    def from_config(cls, limits: LimitsConfig) -> Gate | None:
        if not limits.enabled:
            return None
        gate = cls(
            {
                "chat": limits.chat_per_min,
                "announce": limits.announce_per_min,
                "command": limits.command_per_min,
                "login": limits.login_per_min,
                "read": limits.read_per_min,
                "mirror": limits.mirror_per_min,
            }
        )
        return gate if gate._limiters else None

    def check(self, name: str, client: str) -> float:
        limiter = self._limiters.get(name)
        if limiter is None:
            return 0.0
        return limiter.take(client or "unknown")

    def reset(self) -> None:
        for limiter in self._limiters.values():
            limiter.reset()

    def __contains__(self, name: str) -> bool:
        return name in self._limiters
