"""F17 灯光氛围：两档亮度、边沿触发、退出还原、被手动关灯就退避。"""

from __future__ import annotations

import unittest

from dock_hub.ambience import Ambience
from dock_hub.config import hub_config_to_raw, parse_config


def _ambience(**kwargs) -> tuple[Ambience, dict]:
    """搭一个假世界：set 记录每次调亮，on/bri/read 由测试改。"""
    world: dict = {
        "on": True,
        "brightness": 40,
        "timer": False,
        "reading": False,
        "calls": [],
    }
    cfg = kwargs.pop(
        "config",
        type(
            "C",
            (),
            {
                "device": "desk-led",
                "timer_brightness": 60,
                "reading_brightness": 15,
            },
        )(),
    )
    amb = Ambience(
        cfg,
        timer_active=lambda: world["timer"],
        reading_active=lambda: world["reading"],
        set_brightness=lambda device, level: world["calls"].append((device, level)),
        device_on=lambda device: world["on"],
        read_brightness=lambda device: world["brightness"],
        tick_sec=1.0,
    )
    return amb, world


class AmbienceTest(unittest.TestCase):
    def test_off_when_no_device(self) -> None:
        cfg = type("C", (), {"device": "", "timer_brightness": 60, "reading_brightness": 15})()
        amb, world = _ambience(config=cfg)
        world["timer"] = True
        self.assertEqual(amb.tick().action, "off")
        self.assertEqual(world["calls"], [])

    def test_timer_edge_raises_and_exit_restores(self) -> None:
        amb, world = _ambience()
        self.assertEqual(amb.tick().action, "idle")
        world["timer"] = True
        event = amb.tick()
        self.assertEqual(event.action, "focus")
        self.assertEqual(event.brightness, 60)
        self.assertEqual(world["calls"], [("desk-led", 60)])
        # 计时还在走：边沿只动一次
        self.assertEqual(amb.tick().action, "idle")
        world["timer"] = False
        restore = amb.tick()
        self.assertEqual(restore.action, "restore")
        self.assertEqual(restore.brightness, 40)
        self.assertEqual(world["calls"], [("desk-led", 60), ("desk-led", 40)])

    def test_reading_uses_quiet_level(self) -> None:
        amb, world = _ambience()
        world["reading"] = True
        event = amb.tick()
        self.assertEqual(event.action, "reading")
        self.assertEqual(event.brightness, 15)

    def test_timer_wins_over_reading(self) -> None:
        amb, world = _ambience()
        world["reading"] = True
        world["timer"] = True
        self.assertEqual(amb.tick().action, "focus")

    def test_manual_off_rejects_until_state_clears(self) -> None:
        amb, world = _ambience()
        world["on"] = False
        world["timer"] = True
        self.assertEqual(amb.tick().action, "rejected")
        self.assertEqual(world["calls"], [])
        # 还是关着，也不该反复伸手
        self.assertEqual(amb.tick().action, "rejected")
        # 状态解除（计时结束）：没伸手过，无债可还，但重新武装
        world["timer"] = False
        self.assertEqual(amb.tick().action, "idle")
        world["timer"] = True
        world["on"] = True
        self.assertEqual(amb.tick().action, "focus")

    def test_lamp_offline_rejects_then_rearms(self) -> None:
        amb, world = _ambience()
        world["timer"] = True

        def broken(device: str, level: int) -> None:
            world["calls"].append((device, level))
            raise RuntimeError("offline")

        amb._set = broken
        self.assertEqual(amb.tick().action, "rejected")
        # 修好了：状态没解除也不该再试（本轮已放弃），解除后再来一次
        amb._set = lambda device, level: world["calls"].append((device, level))
        self.assertEqual(amb.tick().action, "idle")
        world["timer"] = False
        amb.tick()
        world["timer"] = True
        self.assertEqual(amb.tick().action, "focus")

    def test_unknown_entry_brightness_skips_restore(self) -> None:
        amb, world = _ambience()
        world["brightness"] = None
        world["timer"] = True
        amb.tick()
        world["timer"] = False
        event = amb.tick()
        self.assertEqual(event.action, "restore")
        self.assertIsNone(event.brightness)
        self.assertEqual(world["calls"], [("desk-led", 60)])


class AmbienceConfigTest(unittest.TestCase):
    def _parse(self, raw: dict) -> object:
        return parse_config(
            {"name": "study", "token": "secret-token-value", "devices": [], **raw}
        )

    def test_off_by_default(self) -> None:
        cfg = self._parse({})
        self.assertEqual(cfg.ambience.device, "")

    def test_full_config_roundtrips(self) -> None:
        cfg = self._parse(
            {
                "devices": [{"id": "desk-led", "name": "灯带", "type": "ws2812", "base_url": "http://led"}],
                "ambience": {"device": "desk-led", "timer_brightness": 70, "reading_brightness": 10},
            }
        )
        self.assertEqual(cfg.ambience.device, "desk-led")
        self.assertEqual(cfg.ambience.timer_brightness, 70)
        self.assertEqual(cfg.ambience.reading_brightness, 10)
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.ambience.device, "desk-led")
        self.assertEqual(again.ambience.timer_brightness, 70)

    def test_unknown_device_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._parse({"ambience": {"device": "ghost"}})

    def test_bad_brightness_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._parse(
                {
                    "devices": [{"id": "desk-led", "name": "灯带", "type": "ws2812", "base_url": "http://led"}],
                    "ambience": {"device": "desk-led", "timer_brightness": 0},
                }
            )

    def test_bad_shape_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._parse({"ambience": "跟着感觉走"})


if __name__ == "__main__":
    unittest.main()
