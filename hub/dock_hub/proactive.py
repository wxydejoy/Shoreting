"""她什么时候可以不等人喊就开口。

守岸人原来的规矩是「不主动开口」（`companion._unsolicited_block` 那道闸）。摆件的
价值恰恰在于不看它的时候也在，所以这里开一个**白名单式的口子**：只有配置里写了
的时刻才会说话，而且三重兜底——

1. 免打扰时段（默认 23:00–07:00）一律不说；
2. 每天配额（默认 6 次）用完就不说，第二天归零；
3. 说出去之前还要过 Companion 那三道闸（手机在不在、是在打游戏、伴侣开没开）。

主动说话只要一次没算准就会变成噪音源，所以这里的每个判断都可以用假时钟验，
不需要真等到早上七点半。

时钟用本地时间（`datetime.now().astimezone()`），Hub 和用户在同一台机器上，
不需要时区配置。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from dock_hub.config import ProactiveConfig

MORNING_WINDOW = timedelta(hours=2)
"""早间播报的有效窗口：到点后两小时内都还值得说，再晚就成了下午茶问候。"""


def _minutes(clock: str) -> int:
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)


def in_quiet_hours(clock: tuple[str, str], now: datetime) -> bool:
    """免打扰时段，允许跨零点（23:00–07:00 就是跨的）。"""
    start, end = _minutes(clock[0]), _minutes(clock[1])
    if start == end:
        return False
    current = now.hour * 60 + now.minute
    if start < end:
        return start <= current < end
    return current >= start or current < end


@dataclass
class Said:
    """一次决定的结果，方便打日志与写测试。"""

    text: str | None = None
    reason: str | None = None

    @property
    def spoke(self) -> bool:
        return bool(self.text)


class Proactive:
    def __init__(
        self,
        config: ProactiveConfig,
        speak: Callable[[str], bool],
        compose_morning: Callable[[datetime], str] | None = None,
    ) -> None:
        self.config = config
        self._speak = speak
        self._compose_morning = compose_morning or (lambda now: "早。今天也慢慢来。")
        self._day: date | None = None
        self._count = 0
        self._morning_day: date | None = None

    def configure(self, config: ProactiveConfig) -> None:
        self.config = config
        # 配额与「今天说过没有」不跟着配置重建，否则改一次 yaml 就能重复播报
        self._reset_if_new_day(datetime.now().astimezone().date())

    def tick(self, now: datetime) -> Said:
        """由后台线程按时钟调；同一分钟内重复调也没关系。"""
        if not self.config.enabled:
            return Said(reason="off")
        self._reset_if_new_day(now.date())
        if in_quiet_hours(self.config.quiet, now):
            return Said(reason="quiet")
        if self.config.daily_limit and self._count >= self.config.daily_limit:
            return Said(reason="quota")
        due = self._morning_due(now)
        if due is None:
            return Said(reason="idle")
        return self._say(due, now)

    def _morning_due(self, now: datetime) -> str | None:
        at = self.config.morning.at
        if not at:
            return None
        if self._morning_day == now.date():
            return None
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            minutes=_minutes(at)
        )
        if now < start or now - start >= MORNING_WINDOW:
            return None
        return self.config.morning.text or self._compose_morning(now)

    def _say(self, text: str, now: datetime) -> Said:
        spoken = text.strip()
        if not spoken:
            return Said(reason="empty")
        if not self._speak(spoken):
            # 手机不在、在打游戏、TTS 挂了：不算配额，下次到点还会说
            return Said(reason="blocked")
        self._count += 1
        self._morning_day = now.date()
        return Said(text=spoken)

    def _reset_if_new_day(self, today: date) -> None:
        if self._day == today:
            return
        self._day = today
        self._count = 0

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "said_today": self._count,
            "daily_limit": self.config.daily_limit,
            "morning_at": self.config.morning.at or None,
        }
