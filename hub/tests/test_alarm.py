"""起床闹钟（F11）：到点爬坡、最后一格开口、错过不补、周末翻篇，全用假时钟。"""

from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path

from dock_hub.alarm import AlarmRamp
from dock_hub.config import parse_config


def _at(year: int = 2026, month: int = 10, day: int = 31, hour: int = 7, minute: int = 0) -> datetime:
    """2026-10-31 是周六——周末用例直接挑它。"""
    return datetime(year, month, day, hour, minute).astimezone()


def _weekday(hour: int = 7, minute: int = 0) -> datetime:
    # 2026-11-02 是周一
    return _at(month=11, day=2, hour=hour, minute=minute)


def _cfg(raw: dict, devices: list[dict] | None = None):
    body = {
        "name": "study",
        "token": "secret-token-value",
        "devices": devices
        or [{"id": "strip", "name": "灯带", "type": "ws2812", "base_url": "http://192.168.14.33"}],
        "mini": False,
        **raw,
    }
    return parse_config(body, Path("/tmp/test-alarm.yaml"))


class AlarmRampTest(unittest.TestCase):
    def _alarm(self, raw: dict | None = None, *, now: datetime, speak: list[str] | None = None):
        config = _cfg({"alarm": raw} if raw else {})
        commands: list[tuple[str, int]] = []

        def speak_fn() -> None:
            if speak is not None:
                speak.append(config.alarm.say)

        alarm = AlarmRamp(
            config.alarm,
            set_brightness=lambda dev, level: commands.append((dev, level)),
            speak=speak_fn,
            now=lambda: now,
        )
        return alarm, commands

    def test_disabled_is_off(self) -> None:
        alarm, commands = self._alarm(now=_weekday())
        self.assertEqual(alarm.tick(_weekday()).action, "off")
        self.assertEqual(commands, [])

    def test_waits_then_ramps(self) -> None:
        alarm, commands = self._alarm(
            {"at": "07:00", "device": "strip", "brightness": 30, "ramp_min": 15},
            now=_weekday(hour=6),
        )
        self.assertEqual(alarm.tick(_weekday(hour=6, minute=59)).action, "waiting")
        first = alarm.tick(_weekday(hour=7, minute=1))
        self.assertEqual(first.action, "ramp")
        self.assertGreaterEqual(first.brightness, 1)
        self.assertLess(first.brightness, 30)
        self.assertEqual(commands, [("strip", first.brightness)])

    def test_snapshot_carries_progress(self) -> None:
        alarm, _ = self._alarm(
            {"at": "07:00", "device": "strip", "ramp_min": 15}, now=_weekday(hour=7, minute=1)
        )
        alarm.tick(_weekday(hour=7, minute=1))
        body = alarm.snapshot()
        assert body is not None
        self.assertGreater(body["total_sec"], 0)
        self.assertLess(body["seconds_left"], 15 * 60)

    def test_fires_on_final_tick_and_stops(self) -> None:
        speak: list[str] = []
        alarm, commands = self._alarm(
            {"at": "07:00", "device": "strip", "brightness": 30, "ramp_min": 15},
            now=_weekday(hour=7),
            speak=speak,
        )
        self.assertEqual(alarm.tick(_weekday(hour=7, minute=13)).action, "ramp")
        fired = alarm.tick(_weekday(hour=7, minute=14, ))
        self.assertEqual(fired.action, "fired")
        self.assertEqual(fired.brightness, 30)
        self.assertEqual(speak, ["起床了。"])
        # 最后一格把灯推满
        self.assertEqual(commands[-1], ("strip", 30))
        # 之后今天不再动手
        self.assertEqual(alarm.tick(_weekday(hour=7, minute=20)).action, "idle")
        self.assertIsNone(alarm.snapshot())

    def test_missed_alarm_does_not_fire_at_noon(self) -> None:
        speak: list[str] = []
        alarm, _ = self._alarm({"at": "07:00"}, now=_weekday(hour=12), speak=speak)
        self.assertEqual(alarm.tick(_weekday(hour=12)).action, "idle")
        self.assertEqual(speak, [])

    def test_weekend_is_skipped(self) -> None:
        speak: list[str] = []
        alarm, commands = self._alarm(
            {"at": "07:00", "device": "strip"}, now=_at(hour=8), speak=speak
        )
        # 2026-10-31 周六，过了点也不叫
        self.assertEqual(alarm.tick(_at(hour=8)).action, "idle")
        self.assertEqual(speak, [])
        self.assertEqual(commands, [])

    def test_no_device_still_fires(self) -> None:
        speak: list[str] = []
        alarm, commands = self._alarm(
            {"at": "07:00", "ramp_min": 15}, now=_weekday(hour=7), speak=speak
        )
        fired = alarm.tick(_weekday(hour=7, minute=14))
        self.assertEqual(fired.action, "fired")
        self.assertEqual(commands, [])
        self.assertEqual(speak, ["起床了。"])

    def test_broken_light_does_not_block_waking(self) -> None:
        speak: list[str] = []
        config = _cfg({"alarm": {"at": "07:00", "device": "strip", "ramp_min": 15}})
        alarm = AlarmRamp(
            config.alarm,
            set_brightness=lambda dev, level: (_ for _ in ()).throw(RuntimeError("offline")),
            speak=lambda: speak.append(config.alarm.say),
            now=lambda: _weekday(hour=7, minute=14),
        )
        self.assertEqual(alarm.tick(_weekday(hour=7, minute=14)).action, "fired")
        self.assertEqual(speak, ["起床了。"])


class AlarmConfigTest(unittest.TestCase):
    def test_parses_defaults(self) -> None:
        cfg = _cfg({"alarm": {"at": "07:00", "device": "strip"}})
        self.assertEqual(cfg.alarm.at, "07:00")
        self.assertTrue(cfg.alarm.weekdays_only)
        self.assertEqual(cfg.alarm.brightness, 30)
        self.assertEqual(cfg.alarm.ramp_min, 15)
        self.assertEqual(cfg.alarm.say, "起床了。")

    def test_empty_at_is_off(self) -> None:
        self.assertFalse(_cfg({"alarm": {"at": ""}}).alarm.at)
        self.assertFalse(_cfg({}).alarm.at)
        self.assertFalse(_cfg({"alarm": False}).alarm.at)

    def test_unknown_device_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _cfg({"alarm": {"at": "07:00", "device": "nope"}})

    def test_bad_at_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _cfg({"alarm": {"at": "25:00"}})

    def test_say_empty_means_silent(self) -> None:
        cfg = _cfg({"alarm": {"at": "07:00", "say": ""}})
        self.assertEqual(cfg.alarm.say, "")

    def test_roundtrip_keeps_alarm(self) -> None:
        from dock_hub.config import hub_config_to_raw

        cfg = _cfg({"alarm": {"at": "06:40", "brightness": 45, "weekdays_only": False}})
        raw = hub_config_to_raw(cfg)
        again = _cfg({"alarm": raw["alarm"]})
        self.assertEqual(again.alarm.at, "06:40")
        self.assertEqual(again.alarm.brightness, 45)
        self.assertFalse(again.alarm.weekdays_only)


if __name__ == "__main__":
    unittest.main()
