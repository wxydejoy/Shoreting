"""主动播报：到点开口之前要过的每一道闸。"""

import unittest
from datetime import datetime, timedelta

from dock_hub.config import AlertsConfig, hub_config_to_raw, parse_config
from dock_hub.proactive import (
    MORNING_WINDOW,
    Proactive,
    SedentaryWatch,
    Sustained,
    in_quiet_hours,
    weather_change_text,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 30, hour, minute)


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.allow = True

    def __call__(self, text: str) -> bool:
        if not self.allow:
            return False
        self.said.append(text)
        return True


def make(config=None, speaker=None, compose=None) -> tuple[Proactive, FakeSpeaker]:
    from dock_hub.config import ProactiveConfig

    speaker = speaker or FakeSpeaker()
    return (
        Proactive(config or ProactiveConfig(enabled=True), speak=speaker, compose_morning=compose),
        speaker,
    )


class TestQuietHours(unittest.TestCase):
    def test_normal_range(self) -> None:
        self.assertTrue(in_quiet_hours(("23:00", "07:00"), at(23, 30)))
        self.assertTrue(in_quiet_hours(("23:00", "07:00"), at(3, 0)))
        self.assertTrue(in_quiet_hours(("23:00", "07:00"), at(6, 59)))
        self.assertFalse(in_quiet_hours(("23:00", "07:00"), at(7, 0)))
        self.assertFalse(in_quiet_hours(("23:00", "07:00"), at(12, 0)))
        self.assertFalse(in_quiet_hours(("23:00", "07:00"), at(22, 59)))

    def test_no_overnight(self) -> None:
        self.assertTrue(in_quiet_hours(("13:00", "14:00"), at(13, 30)))
        self.assertFalse(in_quiet_hours(("13:00", "14:00"), at(2, 0)))

    def test_same_clock_disables(self) -> None:
        self.assertFalse(in_quiet_hours(("07:00", "07:00"), at(7, 0)))


class TestMorning(unittest.TestCase):
    def _proactive(self, speaker=None, text=""):
        from dock_hub.config import MorningConfig, ProactiveConfig

        return Proactive(
            ProactiveConfig(enabled=True, morning=MorningConfig(at="07:30", text=text)),
            speak=speaker or FakeSpeaker(),
            compose_morning=lambda now: f"早。{now.hour}点。",
        )

    def test_before_time_stays_silent(self) -> None:
        bot = self._proactive()
        self.assertEqual(bot.tick(at(7, 0)).reason, "idle")

    def test_says_once_and_only_once(self) -> None:
        speaker = FakeSpeaker()
        bot = self._proactive(speaker)
        first = bot.tick(at(7, 30))
        self.assertTrue(first.spoke)
        self.assertEqual(first.text, "早。7点。")
        # 同一天再怎么到点也不重复
        self.assertEqual(bot.tick(at(7, 35)).reason, "idle")
        self.assertEqual(bot.tick(at(9, 0)).reason, "idle")
        self.assertEqual(len(speaker.said), 1)

    def test_late_within_window_still_says(self) -> None:
        bot = self._proactive()
        late = at(7, 30) + MORNING_WINDOW - timedelta(minutes=1)
        self.assertTrue(bot.tick(late).spoke)

    def test_beyond_window_skips_the_day(self) -> None:
        bot = self._proactive()
        late = at(7, 30) + MORNING_WINDOW + timedelta(minutes=5)
        self.assertEqual(bot.tick(late).reason, "idle")
        # 而且这一天不会再补说（窗口过了就是过了）
        self.assertEqual(bot.tick(at(11, 0)).reason, "idle")

    def test_configured_text_wins(self) -> None:
        speaker = FakeSpeaker()
        bot = self._proactive(speaker, text="早，记得喝水。")
        said = bot.tick(at(8, 0))
        self.assertEqual(said.text, "早，记得喝水。")
        self.assertEqual(speaker.said, ["早，记得喝水。"])

    def test_next_day_again(self) -> None:
        bot = self._proactive()
        self.assertTrue(bot.tick(at(7, 40)).spoke)
        next_day = datetime(2026, 10, 31, 7, 40)
        self.assertTrue(bot.tick(next_day).spoke)


class TestGates(unittest.TestCase):
    def test_disabled(self) -> None:
        from dock_hub.config import ProactiveConfig

        bot, _ = make(ProactiveConfig())
        self.assertEqual(bot.tick(at(8, 0)).reason, "off")

    def test_quiet_beats_due(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        bot, _ = make(
            ProactiveConfig(
                enabled=True, morning=MorningConfig(at="07:30"), quiet=("00:00", "23:59")
            )
        )
        self.assertEqual(bot.tick(at(8, 0)).reason, "quiet")

    def test_quota_beats_due(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        bot, _ = make(
            ProactiveConfig(enabled=True, daily_limit=1, morning=MorningConfig(at="07:30"))
        )
        bot._day = at(7, 45).date()  # 不然 tick 会当成新的一天把配额清零
        bot._count = 1  # 今天已经主动说过一次
        self.assertEqual(bot.tick(at(7, 45)).reason, "quota")

    def test_blocked_does_not_burn_quota(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        speaker = FakeSpeaker()
        speaker.allow = False
        bot = Proactive(
            ProactiveConfig(enabled=True, morning=MorningConfig(at="07:30")),
            speak=speaker,
        )
        self.assertEqual(bot.tick(at(7, 45)).reason, "blocked")
        speaker.allow = True
        said = bot.tick(at(7, 50))
        self.assertTrue(said.spoke, "被拦下不算说过，回来还要说")


class TestConfigure(unittest.TestCase):
    def test_configure_keeps_daily_count(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        speaker = FakeSpeaker()
        bot = Proactive(
            ProactiveConfig(enabled=True, daily_limit=1, morning=MorningConfig(at="07:30")),
            speak=speaker,
        )
        self.assertTrue(bot.tick(at(7, 40)).spoke)
        bot.configure(ProactiveConfig(enabled=True, daily_limit=1))
        self.assertEqual(bot.tick(at(9, 0)).reason, "quota", "改配置不重置当日配额")


class TestConfigParse(unittest.TestCase):
    def test_parse_and_roundtrip(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "proactive": {
                    "daily_limit": 3,
                    "quiet": "23:00-07:30",
                    "morning": {"at": "7:30", "text": "早。"},
                },
                "devices": [],
            }
        )
        self.assertTrue(cfg.proactive.enabled)
        self.assertEqual(cfg.proactive.daily_limit, 3)
        self.assertEqual(cfg.proactive.quiet, ("23:00", "07:30"))
        self.assertEqual(cfg.proactive.morning.at, "07:30")
        self.assertEqual(cfg.proactive.morning.text, "早。")
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.proactive, cfg.proactive)

    def test_off_by_default(self) -> None:
        cfg = parse_config({"name": "study", "token": "secret-token-value", "devices": []})
        self.assertFalse(cfg.proactive.enabled)
        self.assertEqual(cfg.proactive.quiet, ("23:00", "07:00"))
        # 关着就不进 round-trip 的 raw，别把没配的东西写回文件
        self.assertNotIn("proactive", hub_config_to_raw(cfg))

    def test_bad_clock_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "proactive": {"morning": {"at": "七点半"}},
                    "devices": [],
                }
            )

    def test_proactive_false(self) -> None:
        cfg = parse_config(
            {"name": "study", "token": "secret-token-value", "proactive": False, "devices": []}
        )
        self.assertFalse(cfg.proactive.enabled)
        self.assertEqual(cfg.proactive.daily_limit, 6)


class TestAlerts(unittest.TestCase):
    def _bot(self, speaker=None, **kw):
        from dock_hub.config import AlertsConfig, ProactiveConfig

        sp = speaker or FakeSpeaker()
        bot = Proactive(ProactiveConfig(enabled=True, **kw), speak=sp)
        return bot, sp

    def test_says_once_per_day_per_key(self) -> None:
        bot, sp = self._bot()
        self.assertTrue(bot.alert("temp", "有点热。", at(10, 0)).spoke)
        again = bot.alert("temp", "有点热。", at(10, 30))
        self.assertEqual(again.reason, "dup")
        self.assertEqual(len(sp.said), 1)
        # 另一件事是另一件事
        self.assertTrue(bot.alert("offline:lamp", "台灯没应答。", at(10, 35)).spoke)

    def test_next_day_fresh(self) -> None:
        bot, sp = self._bot()
        self.assertTrue(bot.alert("temp", "有点热。", at(10, 0)).spoke)
        tomorrow = datetime(2026, 10, 31, 10, 0)
        self.assertTrue(bot.alert("temp", "有点热。", tomorrow).spoke)

    def test_blocked_does_not_mark(self) -> None:
        bot, sp = self._bot(quiet=("00:00", "23:59"))
        self.assertEqual(bot.alert("temp", "有点热。", at(10, 0)).reason, "quiet")
        # 过了免打扰还能说
        bot.configure(type(bot.config)(enabled=True))
        self.assertTrue(bot.alert("temp", "有点热。", at(10, 0)).spoke)

    def test_quota_shared_with_morning(self) -> None:
        from dock_hub.config import MorningConfig

        bot, sp = self._bot(daily_limit=1, morning=MorningConfig(at="07:30"))
        self.assertTrue(bot.tick(at(7, 40)).spoke)
        self.assertEqual(bot.alert("temp", "有点热。", at(10, 0)).reason, "quota")

    def test_disabled(self) -> None:
        from dock_hub.config import ProactiveConfig

        bot, sp = make(ProactiveConfig())
        self.assertEqual(bot.alert("temp", "有点热。", at(10, 0)).reason, "off")
        self.assertEqual(sp.said, [])


class TestSustained(unittest.TestCase):
    def test_needs_full_duration(self) -> None:
        sus = Sustained()
        t0 = at(10, 0)
        self.assertFalse(sus.check("temp", True, t0, 180))
        self.assertFalse(sus.check("temp", True, t0 + timedelta(seconds=179), 180))
        self.assertTrue(sus.check("temp", True, t0 + timedelta(seconds=180), 180))

    def test_recovery_clears(self) -> None:
        sus = Sustained()
        t0 = at(10, 0)
        sus.check("temp", True, t0, 180)
        self.assertFalse(sus.check("temp", False, t0 + timedelta(minutes=2), 180))
        self.assertFalse(sus.check("temp", True, t0 + timedelta(minutes=3), 180), "修好要重新计时")

    def test_keys_are_independent(self) -> None:
        sus = Sustained()
        t0 = at(10, 0)
        sus.check("temp", True, t0, 180)
        self.assertFalse(sus.check("offline:lamp", True, t0, 180))


class TestConfigAlerts(unittest.TestCase):
    def _parse(self, proactive):
        return parse_config(
            {"name": "study", "token": "secret-token-value", "proactive": proactive, "devices": []}
        )

    def test_defaults(self) -> None:
        cfg = self._parse({"morning": {"at": "07:30"}})
        self.assertEqual(cfg.proactive.alerts.temp_celsius, 85)
        self.assertEqual(cfg.proactive.alerts.offline_minutes, 10)
        self.assertEqual(cfg.proactive.alerts.memory_percent, 90)

    def test_false_disables_both(self) -> None:
        cfg = self._parse({"morning": {"at": "07:30"}, "alerts": False})
        self.assertEqual(cfg.proactive.alerts.temp_celsius, 0)
        self.assertEqual(cfg.proactive.alerts.offline_minutes, 0)

    def test_zero_means_off(self) -> None:
        cfg = self._parse({"alerts": {"temp_celsius": 0}})
        self.assertEqual(cfg.proactive.alerts.temp_celsius, 0)
        self.assertEqual(cfg.proactive.alerts.offline_minutes, 10)

    def test_negative_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._parse({"alerts": {"temp_celsius": -1}})

    def test_memory_threshold_configures(self) -> None:
        cfg = self._parse({"alerts": {"memory_percent": 95}})
        self.assertEqual(cfg.proactive.alerts.memory_percent, 95)
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.proactive.alerts, cfg.proactive.alerts)

    def test_memory_zero_means_off_and_drops_from_raw(self) -> None:
        cfg = self._parse({"alerts": {"memory_percent": 0}})
        self.assertEqual(cfg.proactive.alerts.memory_percent, 0)
        self.assertNotIn("memory_percent", hub_config_to_raw(cfg)["proactive"]["alerts"])

    def test_roundtrip(self) -> None:
        cfg = self._parse({"alerts": {"temp_celsius": 90, "offline_minutes": 5}})
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.proactive.alerts, cfg.proactive.alerts)

    def test_alerts_false_serializes_as_false(self) -> None:
        cfg = self._parse({"alerts": False})
        self.assertEqual(hub_config_to_raw(cfg)["proactive"]["alerts"], False)

    def test_weather_defaults_on(self) -> None:
        cfg = self._parse({"morning": {"at": "07:30"}})
        self.assertTrue(cfg.proactive.alerts.weather)

    def test_weather_can_turn_off(self) -> None:
        cfg = self._parse({"alerts": {"weather": False}})
        self.assertFalse(cfg.proactive.alerts.weather)
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.proactive.alerts, cfg.proactive.alerts)

    def test_alerts_false_turns_weather_off_too(self) -> None:
        cfg = self._parse({"alerts": False})
        self.assertFalse(cfg.proactive.alerts.weather)
        self.assertFalse(cfg.proactive.alerts.temp_celsius)


class TestSpeakUp(unittest.TestCase):
    """companion.speak_up：手机不在、在打游戏就说不了，也不留痕。"""

    def _companion(self):
        from dock_hub.companion import Companion
        from dock_hub.config import CompanionConfig

        bot = Companion(CompanionConfig(enabled=True))
        calls = {"finish": 0}

        def finish(spoken, turn, seq, started, **kw):
            calls["finish"] += 1
            return {"text": spoken, "audio_id": None}

        bot._finish_spoken = finish  # type: ignore[method-assign]
        bot._commit_reply = lambda text: None  # type: ignore[method-assign]
        bot._stop_tts = lambda: None  # type: ignore[method-assign]
        return bot, calls

    def test_says_when_clear(self) -> None:
        bot, calls = self._companion()
        bot._unsolicited_block = lambda: None  # type: ignore[method-assign]
        self.assertTrue(bot.speak_up("早。今天也慢慢来。"))
        self.assertEqual(calls["finish"], 1)

    def test_blocked_by_gate(self) -> None:
        bot, calls = self._companion()
        bot._unsolicited_block = lambda: "disconnected"  # type: ignore[method-assign]
        self.assertFalse(bot.speak_up("早。"))
        self.assertEqual(calls["finish"], 0, "被闸拦下不能走到出声那一步")

    def test_disabled_companion(self) -> None:
        from dock_hub.companion import Companion
        from dock_hub.config import CompanionConfig

        bot = Companion(CompanionConfig(enabled=False))
        self.assertFalse(bot.speak_up("早。"))

    def test_blank_text_is_no(self) -> None:
        bot, _ = self._companion()
        bot._unsolicited_block = lambda: None  # type: ignore[method-assign]
        self.assertFalse(bot.speak_up("   "))


class TestSedentaryWatch(unittest.TestCase):
    def test_needs_full_time(self) -> None:
        watch = SedentaryWatch()
        t0 = at(10, 0)
        self.assertFalse(watch.tick(5.0, t0, 90))
        self.assertFalse(watch.tick(30.0, t0 + timedelta(minutes=89), 90))
        self.assertTrue(watch.tick(30.0, t0 + timedelta(minutes=90), 90))

    def test_break_resets(self) -> None:
        watch = SedentaryWatch()
        t0 = at(10, 0)
        watch.tick(5.0, t0, 90)
        # 离开 6 分钟：休息过了，计时清零
        self.assertFalse(watch.tick(360.0, t0 + timedelta(minutes=89), 90))
        self.assertFalse(watch.tick(5.0, t0 + timedelta(minutes=95), 90), "重新坐，重新计")

    def test_no_data_keeps_counting(self) -> None:
        watch = SedentaryWatch()
        t0 = at(10, 0)
        watch.tick(5.0, t0, 90)
        # 探针没报（None）：不当成在休息，也不清零
        self.assertTrue(watch.tick(None, t0 + timedelta(minutes=95), 90))

    def test_zero_limit_off(self) -> None:
        watch = SedentaryWatch()
        t0 = at(10, 0)
        watch.tick(5.0, t0, 0)
        self.assertFalse(watch.tick(5.0, t0 + timedelta(hours=5), 0))

    def test_reset(self) -> None:
        watch = SedentaryWatch()
        t0 = at(10, 0)
        watch.tick(5.0, t0, 90)
        watch.reset()
        self.assertFalse(watch.tick(5.0, t0 + timedelta(minutes=89), 90))


class TestServiceAlerts(unittest.TestCase):
    """温度与离线的判定在 service 一侧：坏够久才说，说过的不再说。"""

    def _hub(self):
        from dock_hub.config import ProactiveConfig
        from dock_hub.service import DockHub

        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [
                    {
                        "id": "lamp",
                        "name": "台灯",
                        "type": "light",
                        "mijia_name": "台灯",
                        "on_prop": "on-2",
                    },
                    {
                        "id": "game",
                        "name": "无畏契约",
                        "type": "action",
                        "program": "C:\\game.exe",
                    },
                ],
            }
        )
        hub = DockHub(cfg)
        self.addCleanup(hub.proactive.configure, ProactiveConfig())  # 保险：恢复关闭
        hub.proactive.configure(ProactiveConfig(enabled=True, daily_limit=5))
        said: list[str] = []
        hub.proactive._speak = lambda text: (said.append(text), True)[1]
        return hub, said

    def _ready(self, hub, key: str, minutes: float) -> None:
        hub._alert_state._first[key] = datetime.now().astimezone() - timedelta(minutes=minutes)

    def test_temp_alerts_after_sustained_and_once(self) -> None:
        hub, said = self._hub()
        hub.pc_snapshot = lambda: {"online": True, "cpu": {"temp_celsius": 91, "percent": 30}}
        hub._evaluate_alerts()  # 刚开始热：只计时，不开口
        self.assertEqual(said, [])
        self._ready(hub, "temp", minutes=4)
        hub._evaluate_alerts()
        self.assertEqual(said, ["Windows CPU 91 度，有点热。"])
        hub._evaluate_alerts()  # 说过的这轮不再说
        self.assertEqual(said, ["Windows CPU 91 度，有点热。"])

    def test_temp_cool_clears_timer(self) -> None:
        hub, said = self._hub()
        hub.pc_snapshot = lambda: {"online": True, "cpu": {"temp_celsius": 60, "percent": 10}}
        hub._evaluate_alerts()
        self.assertEqual(hub._alert_state._first.get("temp"), None)

    def test_memory_alerts_after_sustained_and_once(self) -> None:
        hub, said = self._hub()
        hub.pc_snapshot = lambda: {
            "online": True,
            "cpu": {"temp_celsius": 50, "percent": 10},
            "memory": {"percent": 94, "used_gb": 30, "total_gb": 32},
        }
        hub._evaluate_alerts()  # 刚开始满：只计时，不开口
        self.assertEqual(said, [])
        self._ready(hub, "mem", minutes=4)
        hub._evaluate_alerts()
        self.assertEqual(said, ["内存用到 94% 了，后台该收一收。"])
        hub._evaluate_alerts()  # 说过的这轮不再说
        self.assertEqual(said, ["内存用到 94% 了，后台该收一收。"])

    def test_memory_frees_clears_timer(self) -> None:
        hub, said = self._hub()
        hub.pc_snapshot = lambda: {
            "online": True,
            "cpu": {"temp_celsius": 50, "percent": 10},
            "memory": {"percent": 60, "used_gb": 19, "total_gb": 32},
        }
        hub._evaluate_alerts()
        self.assertEqual(hub._alert_state._first.get("mem"), None)
        self.assertEqual(said, [])

    def test_memory_off_when_zero(self) -> None:
        from dock_hub.config import ProactiveConfig

        hub, said = self._hub()
        hub.proactive.configure(
            ProactiveConfig(
                enabled=True,
                daily_limit=5,
                alerts=AlertsConfig(temp_celsius=85, offline_minutes=0, sedentary_minutes=0, memory_percent=0),
            )
        )
        hub.pc_snapshot = lambda: {
            "online": True,
            "cpu": {"temp_celsius": 50, "percent": 10},
            "memory": {"percent": 99, "used_gb": 31, "total_gb": 32},
        }
        hub._evaluate_alerts()
        self.assertEqual(said, [])
        self.assertEqual(hub._alert_state._first.get("mem"), None)

    def test_offline_mijia_only(self) -> None:
        hub, said = self._hub()
        with hub._desk_lock:
            hub._desk_devices = [
                {"id": "lamp", "online": False},
                {"id": "game", "online": False},  # 启动项：不提醒
            ]
        self.assertEqual(hub._offline_mijia_devices(), [{"id": "lamp", "name": "台灯"}])
        self._ready(hub, "offline:lamp", minutes=11)
        hub._evaluate_alerts()
        self.assertEqual(said, ["台灯有一阵没应答了。"])

    def test_sedentary_alerts_once_then_retimes(self) -> None:
        hub, said = self._hub()
        hub._idle_seconds = lambda: 10.0  # 一直在动
        hub._sedentary._since = datetime.now().astimezone() - timedelta(minutes=95)
        hub._evaluate_alerts()
        self.assertEqual(said, ["坐了一阵了，起来走走。"])
        hub._evaluate_alerts()  # 提过重新计时，不再连环催
        self.assertEqual(said, ["坐了一阵了，起来走走。"])

    def test_sedentary_break_clears(self) -> None:
        hub, said = self._hub()
        hub._idle_seconds = lambda: 600.0  # 已经离开十分钟
        hub._evaluate_alerts()
        self.assertEqual(said, [])
        self.assertIsNone(hub._sedentary._since)

    def test_telemetry_idle_is_stored(self) -> None:
        hub, _ = self._hub()
        hub.ingest_telemetry({"pc": {"online": True}, "running": [], "idle_sec": 12.5})
        self.assertAlmostEqual(hub._idle_seconds(), 12.5)
        hub.ingest_telemetry({"pc": {"online": True}, "running": [], "idle_sec": "bad"})
        self.assertIsNone(hub._idle_seconds())

    def test_offline_too_soon_is_silent(self) -> None:
        hub, said = self._hub()
        with hub._desk_lock:
            hub._desk_devices = [{"id": "lamp", "online": False}]
        hub._evaluate_alerts()  # 才离线几秒
        self.assertEqual(said, [])


def make_hourly(entries: list[tuple[float, int, float]]) -> dict:
    """构造 fetch_hourly 的返回，entries 从 15:00 起每整点一条。"""
    times, temps, codes, winds = [], [], [], []
    day, hour = 30, 15
    for celsius, code, wind in entries:
        times.append(f"2026-10-{day}T{hour:02d}:00")
        temps.append(celsius)
        codes.append(code)
        winds.append(wind)
        hour += 1
        if hour == 24:
            hour, day = 0, 31
    return {
        "city": "上海",
        "hourly": {"time": times, "celsius": temps, "code": codes, "wind_kmh": winds},
    }


class TestWeatherChange(unittest.TestCase):
    """天气突变：只推「变化」，一次最多挑一条。"""

    def test_rain_when_dry(self) -> None:
        hourly = make_hourly([(20.0, 63, 10.0), (19.0, 63, 10.0)])
        self.assertEqual(
            weather_change_text(at(14, 30), 20.0, "晴", hourly),
            "接下来两小时有雨，出门带伞。",
        )

    def test_no_repeat_if_already_wet(self) -> None:
        hourly = make_hourly([(20.0, 63, 10.0)])
        self.assertIsNone(weather_change_text(at(14, 30), 20.0, "雨", hourly))

    def test_snow(self) -> None:
        hourly = make_hourly([(20.0, 73, 10.0)])
        self.assertEqual(
            weather_change_text(at(14, 30), 20.0, "晴", hourly),
            "接下来两小时要下雪了，路上慢点。",
        )

    def test_temp_drop(self) -> None:
        hourly = make_hourly([(14.0, 1, 5.0), (14.5, 1, 5.0)])
        self.assertEqual(
            weather_change_text(at(14, 30), 20.0, "晴", hourly),
            "过两个钟头要降 6 度，多穿点。",
        )

    def test_wind(self) -> None:
        hourly = make_hourly([(20.0, 1, 42.0)])
        self.assertEqual(
            weather_change_text(at(14, 30), 20.0, "晴", hourly),
            "等会儿风要大了，窗户关严实点。",
        )

    def test_boring_weather_stays_silent(self) -> None:
        hourly = make_hourly([(20.0, 1, 10.0), (20.5, 2, 12.0)])
        self.assertIsNone(weather_change_text(at(14, 30), 20.0, "晴", hourly))

    def test_outside_window_ignored(self) -> None:
        # 20:00 的雨距 now（14:30）超出两小时窗口，不算数
        hourly = {"hourly": {"time": ["2026-10-30T20:00"], "celsius": [20.0], "code": [63], "wind_kmh": [5.0]}}
        self.assertIsNone(weather_change_text(at(14, 30), 20.0, "晴", hourly))

    def test_missing_data_is_silent(self) -> None:
        self.assertIsNone(weather_change_text(at(14, 30), 20.0, "晴", {}))
        self.assertIsNone(weather_change_text(at(14, 30), 20.0, "晴", {"hourly": {"time": []}}))

    def test_rain_beats_drop(self) -> None:
        hourly = make_hourly([(14.0, 63, 5.0)])
        self.assertEqual(
            weather_change_text(at(14, 30), 20.0, "晴", hourly),
            "接下来两小时有雨，出门带伞。",
        )


class TestServiceWeatherAlert(unittest.TestCase):
    """service 一侧：一小时看一次，没城市不打网，夜里不看。"""

    def setUp(self) -> None:
        from dock_hub import weather as weather_mod

        self.weather = weather_mod
        weather_mod._last_city = None
        self._orig = (weather_mod.fetch_hourly, weather_mod.fetch_weather, weather_mod.last_city)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        self.weather.fetch_hourly, self.weather.fetch_weather, self.weather.last_city = self._orig
        self.weather._last_city = None
        self.weather._cache.clear()
        self.weather._hourly_cache.clear()

    def _hub(self):
        from dock_hub.config import ProactiveConfig
        from dock_hub.service import DockHub

        cfg = parse_config({"name": "study", "token": "secret-token-value", "devices": []})
        hub = DockHub(cfg)
        self.addCleanup(hub.proactive.configure, ProactiveConfig())
        hub.proactive.configure(ProactiveConfig(enabled=True, daily_limit=5))
        said: list[str] = []
        hub.proactive._speak = lambda text: (said.append(text), True)[1]
        return hub, said

    def test_says_once_and_then_dup(self) -> None:
        hub, said = self._hub()
        self.weather.last_city = lambda: "上海"
        self.weather.fetch_weather = lambda city: {
            "city": "上海", "celsius": 20.0, "condition": "晴", "query": "上海"
        }
        self.weather.fetch_hourly = lambda city: make_hourly([(20.0, 63, 10.0)])
        hub._evaluate_weather_alert(at(14, 30))
        self.assertEqual(said, ["接下来两小时有雨，出门带伞。"])
        hub._weather_check_at = 0.0  # 模拟一小时后
        hub._evaluate_weather_alert(at(15, 30))
        self.assertEqual(said, ["接下来两小时有雨，出门带伞。"])  # 一天最多一次

    def test_gate_only_checks_hourly(self) -> None:
        hub, said = self._hub()
        calls: list[str] = []
        self.weather.last_city = lambda: "上海"
        self.weather.fetch_weather = lambda city: calls.append(city) or {}
        self.weather.fetch_hourly = lambda city: calls.append(city) or {}
        hub._evaluate_weather_alert(at(14, 30))
        self.assertEqual(len(calls), 2)
        hub._evaluate_weather_alert(at(14, 31))  # 一小时内不再取数据
        self.assertEqual(len(calls), 2)

    def test_no_city_no_network(self) -> None:
        hub, said = self._hub()
        self.weather.last_city = lambda: None

        def boom(city):
            raise AssertionError("不该打网")

        self.weather.fetch_weather = boom
        self.weather.fetch_hourly = boom
        hub._evaluate_weather_alert(at(14, 30))
        self.assertEqual(said, [])

    def test_night_hours_silent(self) -> None:
        hub, said = self._hub()

        def boom(city):
            raise AssertionError("夜里不该打网")

        self.weather.last_city = lambda: "上海"
        self.weather.fetch_weather = boom
        self.weather.fetch_hourly = boom
        hub._evaluate_weather_alert(at(23, 30))
        self.assertEqual(said, [])

    def test_fetch_failure_is_swallowed(self) -> None:
        hub, said = self._hub()
        self.weather.last_city = lambda: "上海"

        def boom(city):
            raise OSError("断网")

        self.weather.fetch_weather = boom
        self.weather.fetch_hourly = boom
        hub._evaluate_weather_alert(at(14, 30))  # 不抛出就算过
        self.assertEqual(said, [])


class TestFinished(unittest.TestCase):
    """任务完成播报：文本、名单、队列。"""

    def test_finished_text(self) -> None:
        from dock_hub.proactive import finished_text

        self.assertEqual(finished_text("Blender.exe", None), "Blender跑完了。")
        self.assertEqual(finished_text("blender.exe", 0.0), "blender跑完了。")
        self.assertEqual(finished_text("a.exe", 42.5), "a跑完了，用了 42 秒。")
        self.assertEqual(finished_text("render.exe", 252.0), "render跑完了，用了 4 分钟。")

    def test_watch_includes_action_processes_and_config(self) -> None:
        hub, _ = self._hub_with_processes()
        watch = hub._finished_watch()
        self.assertIn("valorant-win64-shipping.exe", watch)
        self.assertIn("blender.exe", watch)

    def test_ingest_queues_and_alert_speaks(self) -> None:
        hub, said = self._hub_with_processes()
        hub.ingest_telemetry(
            {"pc": {"online": True}, "running": [], "finished": [{"process": "Blender.exe", "ran_sec": 252.0}]}
        )
        hub._evaluate_alerts()
        self.assertEqual(said, ["Blender跑完了，用了 4 分钟。"])
        hub._evaluate_alerts()  # 队列已倒空，同一天不重复
        self.assertEqual(said, ["Blender跑完了，用了 4 分钟。"])

    def test_unwatched_process_is_dropped(self) -> None:
        hub, said = self._hub_with_processes()
        hub.ingest_telemetry(
            {"pc": {"online": True}, "running": [], "finished": [{"process": "notepad.exe"}]}
        )
        hub._evaluate_alerts()
        self.assertEqual(said, [])

    def test_bad_finished_payload_is_ignored(self) -> None:
        hub, said = self._hub_with_processes()
        hub.ingest_telemetry(
            {"pc": {"online": True}, "running": [], "finished": ["junk", {"process": ""}, 3]}
        )
        hub._evaluate_alerts()
        self.assertEqual(said, [])

    def _hub_with_processes(self):
        from dock_hub.config import ProactiveConfig
        from dock_hub.service import DockHub

        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "devices": [
                    {
                        "id": "valorant",
                        "name": "无畏契约",
                        "type": "action",
                        "program": "C:\\game.exe",
                        "process": ["VALORANT-Win64-Shipping.exe"],
                    }
                ],
                "proactive": {
                    "enabled": True,
                    "daily_limit": 5,
                    "alerts": {"finished": ["Blender.exe"]},
                },
            }
        )
        hub = DockHub(cfg)
        self.addCleanup(hub.reporter.stop)
        self.addCleanup(hub.proactive.configure, ProactiveConfig())
        said: list[str] = []
        hub.proactive._speak = lambda text: (said.append(text), True)[1]
        return hub, said


if __name__ == "__main__":
    unittest.main()
