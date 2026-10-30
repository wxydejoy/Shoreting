"""定时任务：到点开/关设备，或让守岸人说一句话。

只按分钟对齐，进程睡过头就跳过那一次 —— 桌面摆件不需要把漏掉的早安补念一遍。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable, Iterable

from dock_hub.config import ScheduleConfig

TICK_SEC = 15.0


def minute_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M")


def is_due(sched: ScheduleConfig, now: datetime) -> bool:
    if sched.at != now.strftime("%H:%M"):
        return False
    return not sched.weekdays or now.isoweekday() in sched.weekdays


class Scheduler:
    def __init__(
        self,
        schedules: Iterable[ScheduleConfig],
        runner: Callable[[ScheduleConfig], None],
        *,
        tick_sec: float = TICK_SEC,
    ) -> None:
        self._schedules = list(schedules)
        self._runner = runner
        self._tick = max(1.0, float(tick_sec))
        self._lock = threading.Lock()
        self._fired: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_fired_at: str | None = None
        self.last_fired_id: str | None = None
        self.last_error: str | None = None

    @property
    def schedules(self) -> list[ScheduleConfig]:
        return list(self._schedules)

    def start(self) -> None:
        if not self._schedules or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="dock-schedules", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None

    def tick(self, now: datetime | None = None) -> list[str]:
        """检查一次；返回这一分钟里真的跑了的任务 id。"""

        moment = now or datetime.now()
        key = minute_key(moment)
        fired: list[str] = []
        for sched in self._schedules:
            if not is_due(sched, moment):
                continue
            with self._lock:
                if self._fired.get(sched.id) == key:
                    continue
                # 先记下来再执行：任务里慢一点也不会在同一分钟被跑两遍
                self._fired[sched.id] = key
            try:
                self._runner(sched)
            except Exception as exc:  # noqa: BLE001 - 定时任务失败不能带走整个循环
                self.last_error = f"{sched.id}: {exc}"
            else:
                self.last_error = None
            self.last_fired_at = key
            self.last_fired_id = sched.id
            fired.append(sched.id)
        with self._lock:
            stale = [sid for sid, stamp in self._fired.items() if stamp != key]
            for sid in stale:
                self._fired.pop(sid, None)
        return fired

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            self.tick()
