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
from dock_hub.weather import wmo_condition

MORNING_WINDOW = timedelta(hours=2)
"""早间播报的有效窗口：到点后两小时内都还值得说，再晚就成了下午茶问候。"""

WEATHER_WINDOW_HOURS = 2
WEATHER_TEMP_DROP_C = 5.0
WEATHER_WIND_KMH = 40.0
PRECIP_CONDITIONS = frozenset({"雨", "毛毛雨", "阵雨", "雷阵雨", "雷暴冰雹"})
SNOW_CONDITIONS = frozenset({"雪", "阵雪"})


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


def _upcoming_hours(now: datetime, hourly: dict[str, Any]) -> list[tuple[int, float, float]]:
    """未来两小时的 (code, celsius, wind_kmh)；数据残缺的整点直接跳过。

    hourly 的时间串是城市当地时区的（Open-Meteo timezone=auto），按无时区
    本地时间比对；带偏移的串也按时刻取面值——差一两个整点不值得较真。
    """
    data = hourly.get("hourly") or {}
    times = data.get("time") or []
    temps = data.get("celsius") or []
    codes = data.get("code") or []
    winds = data.get("wind_kmh") or []
    now_naive = now.replace(tzinfo=None)
    horizon = now_naive + timedelta(hours=WEATHER_WINDOW_HOURS)
    out: list[tuple[int, float, float]] = []
    for i, raw in enumerate(times):
        try:
            when = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if when.tzinfo is not None:
            when = when.replace(tzinfo=None)
        if not (now_naive < when <= horizon):
            continue
        if i >= len(temps):
            continue
        code = int(codes[i]) if i < len(codes) else 3
        wind = float(winds[i]) if i < len(winds) else 0.0
        out.append((code, float(temps[i]), wind))
    return out


def weather_change_text(
    now: datetime,
    current_celsius: float,
    current_condition: str,
    hourly: dict[str, Any],
) -> str | None:
    """看未来两小时有什么值得提前说一句的变化；没有就 None。

    只推「变化」不推「状态」：现在已经在下雨就不再说有雨，免得天天都开口。
    一次最多挑一条，按雨 > 雪 > 降温 > 大风排——出门最怕的排前面。
    """
    entries = _upcoming_hours(now, hourly)
    if not entries:
        return None
    wet_now = current_condition in PRECIP_CONDITIONS or current_condition in SNOW_CONDITIONS
    if not wet_now:
        for code, _, _ in entries:
            cond = wmo_condition(code)
            if cond in SNOW_CONDITIONS:
                return "接下来两小时要下雪了，路上慢点。"
            if cond in PRECIP_CONDITIONS:
                return "接下来两小时有雨，出门带伞。"
    drop = current_celsius - min(celsius for _, celsius, _ in entries)
    if drop >= WEATHER_TEMP_DROP_C:
        return f"过两个钟头要降 {round(drop)} 度，多穿点。"
    for _, _, wind in entries:
        if wind >= WEATHER_WIND_KMH:
            return "等会儿风要大了，窗户关严实点。"
    return None


class Sustained:
    """某件事要连续坏多久才值得开口。

    温度抖一下不算事，离线几秒也不算；这里只负责计时，
    修好就清零，开口与否（配额、免打扰、一天一次）由 Proactive 决定。
    """

    def __init__(self) -> None:
        self._first: dict[str, datetime] = {}

    def check(self, key: str, bad: bool, now: datetime, seconds: float) -> bool:
        """坏着就计时，够 `seconds` 返回 True；修好清零。"""
        if not bad:
            self._first.pop(key, None)
            return False
        first = self._first.setdefault(key, now)
        return (now - first).total_seconds() >= seconds

    def clear(self, key: str) -> None:
        self._first.pop(key, None)


class SedentaryWatch:
    """连续用电脑多久了。键鼠空闲够 5 分钟就算休息过，计时清零重来。

    `idle` 是键鼠空闲秒数；None 表示探针没报（非 Windows 或拿不到），
    只能当不知道，不提醒也不清零——不能把「没数据」当「在休息」。
    """

    BREAK_SEC = 300.0

    def __init__(self) -> None:
        self._since: datetime | None = None

    def tick(self, idle: float | None, now: datetime, limit_minutes: int) -> bool:
        """True = 该提醒起身了（提醒后由调用方 `reset()` 重新计时）。"""
        if limit_minutes <= 0:
            return False
        if idle is not None and idle >= self.BREAK_SEC:
            self._since = None
            return False
        if self._since is None:
            self._since = now
            return False
        return (now - self._since).total_seconds() >= limit_minutes * 60

    def reset(self) -> None:
        self._since = None


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
        self._alerted: dict[str, date] = {}

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

    def alert(self, key: str, text: str, now: datetime | None = None) -> Said:
        """异常提醒：免打扰与每日配额照走，同一件事一天最多说一次。

        `key` 是这件事的名字（如 `temp` / `offline:lamp`），被配额或免打扰
        拦下时**不**记「说过」——缓一缓（比如过了免打扰）还可以再说。
        """
        now = now or datetime.now().astimezone()
        if not self.config.enabled:
            return Said(reason="off")
        self._reset_if_new_day(now.date())
        if self._alerted.get(key) == now.date():
            return Said(reason="dup")
        if in_quiet_hours(self.config.quiet, now):
            return Said(reason="quiet")
        if self.config.daily_limit and self._count >= self.config.daily_limit:
            return Said(reason="quota")
        said = self._say(text, now)
        if said.spoke:
            self._alerted[key] = now.date()
        return said

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
