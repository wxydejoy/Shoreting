from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from dock_hub.companion import infer_control, skill_prompt
from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.ledstrip import apply_config, percent_to_raw, raw_to_percent
from dock_hub.service import DockHub
from dock_hub.setup_ui import _payload_to_raw


def _cfg(**extra):
    device = {
        "id": "desk-led",
        "name": "灯带",
        "type": "ws2812",
        "base_url": "http://192.168.14.33",
        **extra,
    }
    return parse_config(
        {
            "name": "study",
            "token": "secret-token-value",
            "devices": [device],
            "mini": False,
        }
    )


class MappingTest(unittest.TestCase):
    def test_percent_roundtrip_edges(self) -> None:
        self.assertEqual(percent_to_raw(100), 255)
        self.assertEqual(percent_to_raw(1), 3)
        self.assertEqual(raw_to_percent(0), 0)
        self.assertEqual(raw_to_percent(255), 100)
        self.assertEqual(raw_to_percent(48), 19)


class ConfigTest(unittest.TestCase):
    def test_parses_ws2812(self) -> None:
        cfg = _cfg()
        led = cfg.devices[0]
        self.assertTrue(led.is_ws2812)
        self.assertFalse(led.is_mijia)
        self.assertEqual(led.base_url, "http://192.168.14.33")
        self.assertEqual(led.on_brightness, 48)
        self.assertTrue(led.follow_pc)

    def test_requires_base_url(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "devices": [{"id": "desk-led", "name": "灯带", "type": "ws2812"}],
                }
            )

    def test_roundtrip(self) -> None:
        cfg = _cfg(on_brightness=80, follow_pc=False)
        raw = hub_config_to_raw(cfg)["devices"][0]
        self.assertEqual(raw["type"], "ws2812")
        self.assertEqual(raw["base_url"], "http://192.168.14.33")
        self.assertEqual(raw["on_brightness"], 80)
        self.assertFalse(raw["follow_pc"])
        again = parse_config({**hub_config_to_raw(cfg), "token": "secret-token-value", "mini": False})
        self.assertEqual(again.devices[0].on_brightness, 80)
        self.assertFalse(again.devices[0].follow_pc)


class ClientTest(unittest.TestCase):
    def test_apply_posts_json(self) -> None:
        captured: dict = {}

        class Fake:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok":true}'

        def fake_open(req, timeout=0):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Fake()

        with patch("dock_hub.ledstrip.urllib.request.urlopen", fake_open):
            apply_config("http://192.168.14.33/", {"brightness": 0}, timeout_sec=1.5)
        self.assertEqual(captured["url"], "http://192.168.14.33/api/config")
        self.assertEqual(captured["body"], {"brightness": 0})
        self.assertEqual(captured["timeout"], 1.5)


class HubCommandTest(unittest.TestCase):
    def test_command_on_off_and_snapshot_type_light(self) -> None:
        posts: list[dict] = []

        def fake_apply(base_url, fields, timeout_sec=2.0):
            posts.append({"url": base_url, "fields": dict(fields), "timeout": timeout_sec})

        def fake_read(base_url, timeout_sec=2.0):
            last = posts[-1]["fields"] if posts else {}
            brightness = int(last.get("brightness", 48))
            return {
                "effect": 2,
                "brightness": brightness,
                "speed": 45,
                "color_r": 0,
                "color_g": 200,
                "color_b": 255,
            }

        with (
            patch("dock_hub.service.apply_config", fake_apply),
            patch("dock_hub.service.read_config", fake_read),
        ):
            hub = DockHub(_cfg())
            off = hub.command("desk-led", {"on": False})
            self.assertEqual(off["type"], "light")
            self.assertEqual(off["id"], "desk-led")
            self.assertFalse(off["on"])
            self.assertEqual(posts[-1]["fields"], {"brightness": 0})

            on = hub.command("desk-led", {"on": True})
            self.assertTrue(on["on"])
            self.assertEqual(posts[-1]["fields"], {"brightness": 48})

            bri = hub.command("desk-led", {"on": True, "brightness": 40})
            self.assertEqual(bri["brightness"], 40)
            self.assertEqual(posts[-1]["fields"]["brightness"], percent_to_raw(40))

            snap = hub.snapshot()
            ids = [d["id"] for d in snap["devices"]]
            self.assertIn("desk-led", ids)
            led = next(d for d in snap["devices"] if d["id"] == "desk-led")
            self.assertEqual(led["type"], "light")

    def test_follow_pc_on_off(self) -> None:
        posts: list[dict] = []

        def fake_apply(base_url, fields, timeout_sec=2.0):
            posts.append(dict(fields))

        def fake_read(base_url, timeout_sec=2.0):
            brightness = int(posts[-1].get("brightness", 0)) if posts else 0
            return {"brightness": brightness, "effect": 2, "speed": 45, "color_r": 0, "color_g": 200, "color_b": 255}

        with (
            patch("dock_hub.service.apply_config", fake_apply),
            patch("dock_hub.service.read_config", fake_read),
        ):
            hub = DockHub(_cfg())
            hub.follow_pc(True)
            self.assertEqual(posts[-1], {"brightness": 48})
            hub.follow_pc(False)
            self.assertEqual(posts[-1], {"brightness": 0})

    def test_snapshot_without_mijia_still_lists_led(self) -> None:
        with (
            patch("dock_hub.service.apply_config"),
            patch("dock_hub.service.read_config", return_value={"brightness": 48}),
        ):
            hub = DockHub(_cfg())
            snap = hub.snapshot()
            self.assertEqual(snap["hub"]["mijia"], "login_required")
            self.assertEqual(snap["devices"][0]["id"], "desk-led")
            self.assertEqual(snap["devices"][0]["type"], "light")

    def test_companion_skill_and_infer(self) -> None:
        with (
            patch("dock_hub.service.apply_config"),
            patch("dock_hub.service.read_config", return_value={"brightness": 48}),
        ):
            hub = DockHub(_cfg())
            skills = hub.companion._skills
            self.assertTrue(any(s["id"] == "desk-led" and s.get("kind") == "toggle" for s in skills))
            self.assertIn("灯带", skill_prompt(skills))
            self.assertEqual(infer_control("关掉灯带。", skills), [("desk-led", False)])
            self.assertEqual(infer_control("打开灯带。", skills), [("desk-led", True)])

    def test_follow_pc_restores_saved_brightness(self) -> None:
        import tempfile
        from pathlib import Path

        posts: list[dict] = []

        def fake_apply(base_url, fields, timeout_sec=2.0):
            posts.append(dict(fields))

        def fake_read(base_url, timeout_sec=2.0):
            brightness = int(posts[-1].get("brightness", 0)) if posts else 0
            return {"brightness": brightness}

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "hub.yaml"
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "devices": [
                    {
                        "id": "desk-led",
                        "name": "灯带",
                        "type": "ws2812",
                        "base_url": "http://192.168.14.33",
                    }
                ],
                "mini": False,
            },
            path,
        )
        with (
            patch("dock_hub.service.apply_config", fake_apply),
            patch("dock_hub.service.read_config", fake_read),
        ):
            hub = DockHub(cfg)
            hub.command("desk-led", {"on": True, "brightness": 80})
            hub.follow_pc(False)
            self.assertEqual(posts[-1], {"brightness": 0})
            hub2 = DockHub(cfg)
            hub2.follow_pc(True)
            self.assertEqual(posts[-1]["brightness"], percent_to_raw(80))

        cfg = _cfg()
        raw = _payload_to_raw(
            {
                "name": "study",
                "token": "secret-token-value",
                "port": 17890,
                "devices": [{"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯"}],
            },
            cfg,
        )
        types = {d["type"] for d in raw["devices"]}
        self.assertIn("ws2812", types)
        self.assertIn("light", types)


if __name__ == "__main__":
    unittest.main()
