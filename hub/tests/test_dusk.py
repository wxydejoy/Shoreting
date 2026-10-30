"""日落联动（F16）：触发时刻、爬坡、被拒，全用假时钟验，不等天黑。"""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from dock_hub.config import parse_config
from dock_hub.dusk import DuskEvent, DuskRamp


def _at(hour: int, minute: int = 0) -> datetime:
    today = datetime.now().astimezone().date()
    return datetime(today.year, today.month, today.day, hour, minute).astimezone()


def _cfg(raw: dict, devices: list[dict] | None = None):
    body = {
        "name": "study",
        "token": "secret-token-value",
        "devices": devices
        or [{"id": "strip", "name": "灯带", "type": "ws2812", "base_url": "http://192.168.14.33"}],
        "mini": False,
        **raw,
    }
    return parse_config(body, Path("/tmp/test-dusk.yaml"))


class DuskRampTest(unittest.TestCase):
    def _ramp(self, raw: dict | None = None, *, on: bool = True):
        config = _cfg({"dusk": raw} if raw else {})
        commands: list[tuple[str, int]] = []
        ramp = DuskRamp(
            config.dusk,
            sunset=lambda: "17:30",
            set_brightness=lambda dev, level: commands.append((dev, level)),
            device_on=lambda dev: on,
            now=lambda: _at(12),
        )
        return ramp, commands

    def test_disabled_is_off(self) -> None:
        ramp, commands = self._ramp()
        self.assertEqual(ramp.tick(_at(18)).action, "off")
        self.assertEqual(commands, [])

    def test_waits_until_sunset_plus_offset(self) -> None:
        ramp, commands = self._ramp({"device": "strip"})
        self.assertEqual(ramp.tick(_at(17, 59)).action, "waiting")
        self.assertEqual(commands, [])
        first = ramp.tick(_at(18, 1))  # 日落 17:30 + 30 分钟 = 18:00
        self.assertEqual(first.action, "ramp")
        self.assertEqual(commands, [("strip", first.brightness)])
        self.assertGreaterEqual(first.brightness, 1)

    def test_ramps_up_to_target_and_stops(self) -> None:
        ramp, commands = self._ramp(
            {"device": "strip", "offset_min": 0, "ramp_min": 30, "brightness": 40}
        )
        self.assertEqual(ramp.tick(_at(17, 30)).action, "ramp")
        self.assertEqual(ramp.tick(_at(17, 45)).brightness, 20)
        done = ramp.tick(_at(18, 0))
        self.assertEqual(done.action, "done")
        self.assertEqual(done.brightness, 40)
        # 推满之后今天就不动手了
        self.assertEqual(ramp.tick(_at(18, 5)).action, "idle")
        self.assertEqual(len(commands), 3)

    def test_stops_pushing_after_until(self) -> None:
        ramp, commands = self._ramp(
            {"device": "strip", "offset_min": 0, "ramp_min": 30, "until": "18:00"}
        )
        self.assertEqual(ramp.tick(_at(17, 40)).action, "ramp")
        self.assertEqual(ramp.tick(_at(18, 0)).action, "idle")
        self.assertEqual(len(commands), 1)

    def test_manual_off_is_rejected_for_the_day(self) -> None:
        ramp, commands = self._ramp({"device": "strip"}, on=False)
        self.assertEqual(ramp.tick(_at(18, 10)).action, "rejected")
        self.assertEqual(commands, [])
        self.assertEqual(ramp.tick(_at(18, 12)).action, "idle")

    def test_no_sunset_means_waiting(self) -> None:
        config = _cfg({"dusk": {"device": "strip"}})
        ramp = DuskRamp(
            config.dusk,
            sunset=lambda: None,
            set_brightness=lambda dev, level: None,
            now=lambda: _at(18),
        )
        self.assertEqual(ramp.tick(_at(18)).action, "no_city")

    def test_device_failure_closes_the_day(self) -> None:
        config = _cfg({"dusk": {"device": "strip"}})
        ramp = DuskRamp(
            config.dusk,
            sunset=lambda: "17:30",
            set_brightness=lambda dev, level: (_ for _ in ()).throw(RuntimeError("offline")),
            now=lambda: _at(18, 1),
        )
        self.assertEqual(ramp.tick(_at(18, 1)).action, "rejected")
        self.assertEqual(ramp.tick(_at(18, 2)).action, "idle")


class DuskConfigTest(unittest.TestCase):
    def test_parses_and_defaults(self) -> None:
        cfg = _cfg({"dusk": {"device": "strip", "city": "杭州"}})
        self.assertTrue(cfg.dusk.enabled)
        self.assertEqual(cfg.dusk.device, "strip")
        self.assertEqual(cfg.dusk.city, "杭州")
        self.assertEqual(cfg.dusk.offset_min, 30)
        self.assertEqual(cfg.dusk.until, "22:00")
        self.assertEqual(cfg.dusk.brightness, 40)
        self.assertEqual(cfg.dusk.ramp_min, 45)

    def test_unknown_device_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _cfg({"dusk": {"device": "nope"}})

    def test_bad_until_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _cfg({"dusk": {"device": "strip", "until": "25:00"}})

    def test_brightness_bounds(self) -> None:
        with self.assertRaises(ValueError):
            _cfg({"dusk": {"device": "strip", "brightness": 0}})
        with self.assertRaises(ValueError):
            _cfg({"dusk": {"device": "strip", "brightness": 101}})

    def test_roundtrip_keeps_dusk(self) -> None:
        from dock_hub.config import hub_config_to_raw

        cfg = _cfg({"dusk": {"device": "strip", "brightness": 55}})
        raw = hub_config_to_raw(cfg)
        again = _cfg({"dusk": raw["dusk"]})
        self.assertEqual(again.dusk.brightness, 55)
        self.assertEqual(again.dusk.device, "strip")


class DuskEventTest(unittest.TestCase):
    def test_event_carries_brightness(self) -> None:
        event = DuskEvent("ramp", 12)
        self.assertEqual(event.brightness, 12)
        self.assertIsNone(DuskEvent("waiting").brightness)


if __name__ == "__main__":
    unittest.main()
