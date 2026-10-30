"""日落联动（F16）：天黑灯自己亮，比任何语音都更像有人住。

规矩：
- 日落后 ``offset_min`` 分钟动手，``ramp_min`` 分钟内把亮度从 0 推到目标；
- ``until`` 之后就不再推——睡前场景接着把它降下去，这条链不越界；
- 推亮期间发现灯被人手动关了，算今天被拒了，明天再说；
- 全程不说话：这不是主动播报，是「家」本身，所以也不挂在配额体系里。

日落时间来自 ``weather.fetch_sun``，城市用配置里写死的，没写就用手机最近
查天气的那座（``weather.last_city``）。两处都没有就安静等着——总有一晚会有的。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from dock_hub.config import DuskConfig


@dataclass
class DuskEvent:
    """一次 tick 的结果，打日志和写测试用。"""

    action: str  # off / no_city / waiting / ramp / done / rejected / idle
    brightness: int | None = None


class DuskRamp:
    """日落后的灯带爬坡。自己起线程，每分钟看一眼。"""

    TICK_SEC = 60.0

    def __init__(
        self,
        config: DuskConfig,
        *,
        sunset: Callable[[], str | None],
        set_brightness: Callable[[str, int], None],
        device_on: Callable[[str], bool] | None = None,
        now: Callable[[], datetime] | None = None,
        tick_sec: float = TICK_SEC,
    ) -> None:
        self.config = config
        self._sunset = sunset
        self._set = set_brightness
        self._device_on = device_on
        self._now = now or (lambda: datetime.now().astimezone())
        self._tick = max(1.0, float(tick_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 当天爬坡结束（推满 / 被拒 / 过了 until）就不再动手，明天重来
        self._closed_day = None
        self.last_event: DuskEvent | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="dusk", daemon=True)
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
            except Exception as exc:  # noqa: BLE001 - 联动挂了不能拖垮 Hub
                print(f"[dusk] 出错：{exc}")

    def tick(self, now: datetime | None = None) -> DuskEvent:
        """检查一次。测试可以直接喂假时钟，不靠线程。"""
        moment = now or self._now()
        event = self._evaluate(moment)
        self.last_event = event
        return event

    def _evaluate(self, now: datetime) -> DuskEvent:
        cfg = self.config
        if not cfg.enabled or not cfg.device:
            return DuskEvent("off")
        if self._closed_day == now.date():
            return DuskEvent("idle")
        at = _hhmm_minutes(self._sunset_of(now))
        if at is None:
            return DuskEvent("no_city")
        start = _today_at(now, at + max(0, cfg.offset_min))
        until = _today_at(now, _hhmm_minutes(cfg.until))
        if now >= until:
            self._closed_day = now.date()
            return DuskEvent("idle")
        if until <= start:
            # until 早于日落：这条配置永远不该动手，今天就算过完了
            self._closed_day = now.date()
            return DuskEvent("idle")
        if now < start:
            return DuskEvent("waiting")
        if self._device_on is not None and not self._device_on(cfg.device):
            # 手动关掉 = 今天被拒了。灯不跟人抢。
            self._closed_day = now.date()
            return DuskEvent("rejected")
        ramp_sec = max(60.0, cfg.ramp_min * 60.0)
        progress = (now - start).total_seconds() / ramp_sec
        level = max(1, round(cfg.brightness * min(1.0, progress)))
        try:
            self._set(cfg.device, level)
        except Exception:
            # 设备叫不动（离线 / 网关挂了）：今天放弃，别每分钟刷一遍错误
            self._closed_day = now.date()
            return DuskEvent("rejected")
        if progress >= 1.0:
            self._closed_day = now.date()
            return DuskEvent("done", level)
        return DuskEvent("ramp", level)

    def _sunset_of(self, now: datetime) -> str | None:
        """今天这座城的日落，"HH:MM"。拿不到就 None，下个 tick 再问。"""
        return self._sunset()


def _today_at(now: datetime, minutes: int) -> datetime:
    """当天的某时刻，允许越过 24 点（26:10 算第二天凌晨）——until 写过点也能用。"""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day + timedelta(minutes=minutes)


def _hhmm_minutes(clock: str | None) -> int | None:
    """给 service 用的兜底解析；parse_config 已经校过一次，这里不再抛。"""
    if not clock:
        return None
    try:
        hour_s, minute_s = str(clock).strip().split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
    except ValueError:
        return None
    if not (0 <= hour <= 47 and 0 <= minute < 60):
        return None
    return hour * 60 + minute
