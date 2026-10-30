"""起床闹钟（F11）：她比你先醒。

规矩：
- ``at`` 到点后 ``ramp_min`` 分钟内把 ``device`` 亮度从 0 平滑推到
  ``brightness``，进度同时放进 snapshot，手机屏幕跟着爬亮；
- 爬坡的最后一格轻声说 ``say``——灯亮够了人才睁眼，声音是收尾不是闹铃；
- 灯叫不动不连累人醒：记一笔就完，屏幕和声音照旧；
- 错过了不补（Hub 中午才起来，早上那声就免了）；一天只醒一次；
- ``weekdays_only`` 周末整条翻篇。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from dock_hub.config import AlarmConfig


@dataclass
class AlarmEvent:
    """一次 tick 的结果，打日志和写测试用。"""

    action: str  # off / waiting / ramp / fired / idle
    brightness: int | None = None


class AlarmRamp:
    """起床爬坡。自己起线程，每分钟看一眼；snapshot 进度给手机用。"""

    TICK_SEC = 60.0

    def __init__(
        self,
        config: AlarmConfig,
        *,
        set_brightness: Callable[[str, int], None],
        speak: Callable[[], None] | None = None,
        now: Callable[[], datetime] | None = None,
        tick_sec: float = TICK_SEC,
    ) -> None:
        self.config = config
        self._set = set_brightness
        self._speak = speak
        self._now = now or (lambda: datetime.now().astimezone())
        self._tick = max(1.0, float(tick_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fired_day = None
        # 最近一次评估出的爬坡窗口，snapshot 随时要，不能现算
        self._ramp_end: datetime | None = None
        self._ramp_total = 0.0
        self.last_event: AlarmEvent | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="alarm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - 闹钟挂了不能拖垮 Hub
                print(f"[alarm] 出错：{exc}")

    def tick(self, now: datetime | None = None) -> AlarmEvent:
        """检查一次。测试可以直接喂假时钟，不靠线程。"""
        moment = now or self._now()
        event = self._evaluate(moment)
        self.last_event = event
        return event

    def snapshot(self) -> dict[str, float] | None:
        """爬坡中的进度（秒），给手机屏幕渐亮用；没在爬就 None。"""
        if self._ramp_end is None:
            return None
        now = self._now()
        left = (self._ramp_end - now).total_seconds()
        if left <= 0 or self._ramp_total <= 0:
            return None
        return {"seconds_left": round(left, 1), "total_sec": round(self._ramp_total, 1)}

    def _evaluate(self, now: datetime) -> AlarmEvent:
        cfg = self.config
        if not cfg.at:
            return AlarmEvent("off")
        today = now.date()
        if self._fired_day == today:
            self._ramp_end = None
            return AlarmEvent("idle")
        if cfg.weekdays_only and today.weekday() >= 5:
            return AlarmEvent("idle")
        start = _today_at(now, _hhmm_minutes(cfg.at))
        ramp_sec = max(60.0, cfg.ramp_min * 60.0)
        end = start + timedelta(seconds=ramp_sec)
        if now < start:
            return AlarmEvent("waiting")
        if now >= end:
            # 一觉睡过了（Hub 重启到中午之类）：不补，不吵
            self._fired_day = today
            self._ramp_end = None
            return AlarmEvent("idle")
        self._ramp_end = end
        self._ramp_total = ramp_sec
        progress = (now - start).total_seconds() / ramp_sec
        if cfg.device:
            level = max(1, round(cfg.brightness * min(1.0, progress)))
            try:
                self._set(cfg.device, level)
            except Exception:  # noqa: BLE001 - 灯坏了不连累醒
                print(f"[alarm] 灯没推上去，屏幕照旧")
        if now + timedelta(seconds=self._tick) >= end:
            # 最后一格：灯推满，轻声说一句，今天就算醒过了
            self._fired_day = today
            if cfg.device:
                try:
                    self._set(cfg.device, cfg.brightness)
                except Exception:  # noqa: BLE001
                    pass
            if self._speak is not None and cfg.say:
                try:
                    self._speak()
                except Exception as exc:  # noqa: BLE001
                    print(f"[alarm] 唤醒那句没说出去：{exc}")
            return AlarmEvent("fired", cfg.brightness)
        level = max(1, round(cfg.brightness * progress))
        return AlarmEvent("ramp", level)


def _today_at(now: datetime, minutes: int | None) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day + timedelta(minutes=minutes or 0)


def _hhmm_minutes(clock: str) -> int:
    """parse_config 校过的 "HH:MM"，这里只管翻成分钟。"""
    hour_s, minute_s = str(clock).strip().split(":", 1)
    return int(hour_s) * 60 + int(minute_s)
