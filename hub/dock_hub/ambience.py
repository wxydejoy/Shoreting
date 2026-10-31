"""灯光氛围（F17）：灯带亮度跟着「桌面在干嘛」走。

蓝图原话是「随音乐 / 随专注状态变色」。变色这条路走不通——灯带固件只收
on/brightness，米家侧这台灯也没有色温 props——拍脑袋的假律动比没有更糟，
所以落成**亮度随专注状态**：

- 番茄钟/计时在走 → 专注档（默认 60%）：干活时灯该立起来一点；
- 睡前朗读在念 → 睡前档（默认 15%）：听书时灯该暗下去；
- 都没有 → 回到进来前的那一档（拿不到就原地不动，不瞎猜）。

规矩（沿 F16 日落联动）：
- 只在状态开始/结束的**边沿**动一次，不逐分钟刷；
- 动手前灯被人手动关了，这次就不动手，等状态解除再重新武装——灯不跟人抢；
- 计时和朗读同时发生时，计时优先（专注档更亮，暗下去反而误事）；
- 全程不说话、不占配额：这是「家」本身，不是主动播报。

和日落联动（F16）/起床闹钟（F11）共用一条 ``command`` 通道；若同一盏灯
同时配了日落爬坡和睡前档，两股会互相覆盖——把 dusk 的 until 配早一点，
或 ambience 换一盏灯。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from dock_hub.config import AmbienceConfig


@dataclass
class AmbienceEvent:
    """一次 tick 的结果，打日志和写测试用。"""

    action: str  # off / focus / reading / restore / rejected / idle
    brightness: int | None = None


class Ambience:
    """专注/朗读两档氛围。自己起线程，每半分钟看一眼，边沿才动手。"""

    TICK_SEC = 30.0

    def __init__(
        self,
        config: AmbienceConfig,
        *,
        timer_active: Callable[[], bool],
        reading_active: Callable[[], bool],
        set_brightness: Callable[[str, int], None],
        device_on: Callable[[str], bool] | None = None,
        read_brightness: Callable[[str], int | None] | None = None,
        tick_sec: float = TICK_SEC,
    ) -> None:
        self.config = config
        self._timer_active = timer_active
        self._reading_active = reading_active
        self._set = set_brightness
        self._device_on = device_on
        self._read_brightness = read_brightness
        self._tick = max(1.0, float(tick_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: str | None = None  # None / "focus" / "reading"
        self._backed_off = False  # 被手动关过：这一轮状态解除前不再伸手
        self._captured: int | None = None  # 进氛围前的亮度，退出时还回去
        self.last_event: AmbienceEvent | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="ambience", daemon=True)
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
                print(f"[ambience] 出错：{exc}")

    def tick(self) -> AmbienceEvent:
        """检查一次。线程每半分钟调；测试直接调，不用等。"""
        cfg = self.config
        if not cfg.device:
            event = AmbienceEvent("off")
        else:
            event = self._evaluate()
        self.last_event = event
        return event

    def _evaluate(self) -> AmbienceEvent:
        desired = self._desired()
        if desired is None:
            if self._active is not None:
                return self._release()
            # 本来就没伸手过：状态解除就是重新武装（被拒过的这次清账）
            self._backed_off = False
            self._captured = None
            return AmbienceEvent("idle")
        if desired == self._active:
            return AmbienceEvent("idle")
        if self._backed_off:
            # 上次把灯叫挂了：这一轮状态没解除前不再伸手
            return AmbienceEvent("idle")
        if self._device_on is not None and not self._device_on(self.config.device):
            # 灯是灭的 = 人自己关的。氛围不当「没看见」。
            return AmbienceEvent("rejected")
        if self._active is None:
            self._captured = (
                self._read_brightness(self.config.device)
                if self._read_brightness is not None
                else None
            )
        level = (
            self.config.timer_brightness
            if desired == "focus"
            else self.config.reading_brightness
        )
        try:
            self._set(self.config.device, level)
        except Exception:
            # 灯叫不动（离线/网关挂了）：这轮放弃，状态解除后重新武装
            self._backed_off = True
            return AmbienceEvent("rejected")
        self._active = desired
        return AmbienceEvent("focus" if desired == "focus" else "reading", level)

    def _desired(self) -> str | None:
        """计时优先于朗读。两个都空 = 桌面归人管，氛围退场。"""
        if self._timer_active():
            return "focus"
        if self._reading_active():
            return "reading"
        return None

    def _release(self) -> AmbienceEvent:
        """状态解除：还回进来前的亮度，重新武装。人手动关了的灯不复活。"""
        was_active = self._active
        self._active = None
        self._backed_off = False
        captured = self._captured
        self._captured = None
        if (
            was_active
            and captured is not None
            and captured > 0
            and (self._device_on is None or self._device_on(self.config.device))
        ):
            try:
                self._set(self.config.device, captured)
            except Exception:
                return AmbienceEvent("rejected")
            return AmbienceEvent("restore", captured)
        return AmbienceEvent("restore")
