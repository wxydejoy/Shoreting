from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from dock_hub.companion import Companion
from dock_hub.config import CompanionConfig, hub_config_to_raw, parse_config
from dock_hub.errors import HubError
from dock_hub.server import make_handler
from dock_hub.service import DockHub

TOKEN = "secret-token-value"


def _raw(**over):
    base = {
        "name": "study",
        "token": TOKEN,
        "host": "127.0.0.1",
        "port": 0,
        "mini": False,
        "devices": [
            {
                "id": "lamp",
                "name": "台灯",
                "type": "light",
                "mijia_name": "台灯",
                "brightness_prop": "brightness",
            },
            {"id": "plug", "name": "插座", "type": "switch", "mijia_name": "插座"},
        ],
        "scenes": [
            {
                "id": "bedtime",
                "name": "晚安",
                "off": ["lamp", "plug"],
                "say": "灯关了，早点睡。",
                "phrases": ["我要睡了", "睡觉了"],
            }
        ],
    }
    base.update(over)
    return base


def _with_steam():
    return _raw()["devices"] + [
        {"id": "steam", "name": "Steam", "type": "action", "run": {"program": "/bin/echo"}}
    ]


class SceneConfigTest(unittest.TestCase):
    def test_parses_scene_fields(self) -> None:
        cfg = parse_config(_raw())
        self.assertEqual(len(cfg.scenes), 1)
        scene = cfg.scenes[0]
        self.assertEqual(scene.id, "bedtime")
        self.assertEqual(scene.off, ["lamp", "plug"])
        self.assertEqual(scene.say, "灯关了，早点睡。")
        self.assertEqual(scene.triggers(), ["我要睡了", "睡觉了", "晚安"])

    def test_brightness_and_on(self) -> None:
        cfg = parse_config(
            _raw(
                scenes=[
                    {
                        "id": "work",
                        "name": "干活",
                        "on": ["lamp"],
                        "brightness": {"lamp": 80},
                    }
                ]
            )
        )
        self.assertEqual(cfg.scenes[0].on, ["lamp"])
        self.assertEqual(cfg.scenes[0].brightness, {"lamp": 80})
        self.assertEqual(cfg.scenes[0].triggers(), ["干活"])

    def test_yaml_boolean_keys_still_reach_on_off(self) -> None:
        """真 YAML 文本里裸的 on/off 会被 PyYAML 变成 True/False 键——开关列表不能被它吃掉。"""
        raw = yaml.safe_load(
            "name: study\n"
            "token: secret\n"
            "devices:\n"
            "  - id: lamp\n"
            "    name: 台灯\n"
            "    type: light\n"
            "    mijia_name: 台灯\n"
            "scenes:\n"
            "  - id: bedtime\n"
            "    name: 睡前\n"
            "    on: [lamp]\n"
            "    say: 灯关了\n"
        )
        scene = parse_config(raw).scenes[0]
        self.assertEqual(scene.on, ["lamp"])

        # safe_load 之后的等价形态：布尔键直接出现在 dict 里也得认
        cfg = parse_config(_raw(scenes=[{"id": "x", "name": "X", True: ["lamp"]}]))
        self.assertEqual(cfg.scenes[0].on, ["lamp"])

    def test_unknown_device_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_config(_raw(scenes=[{"id": "x", "name": "X", "on": ["ghost"]}]))
        self.assertIn("ghost", str(ctx.exception))

    def test_action_device_is_rejected(self) -> None:
        devices = _raw()["devices"] + [
            {"id": "steam", "name": "Steam", "type": "action", "run": {"program": "/bin/echo"}}
        ]
        with self.assertRaises(ValueError) as ctx:
            parse_config(_raw(devices=devices, scenes=[{"id": "x", "name": "X", "on": ["steam"]}]))
        self.assertIn("启动项", str(ctx.exception))

    def _with_steam(self):
        return _with_steam()

    def test_run_accepts_action_devices(self) -> None:
        cfg = parse_config(
            _raw(
                devices=_with_steam(),
                scenes=[{"id": "work", "name": "开工", "run": ["steam"]}],
            )
        )
        self.assertEqual(cfg.scenes[0].run, ["steam"])

    def test_run_rejects_non_action_devices(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_config(
                _raw(scenes=[{"id": "x", "name": "X", "run": ["lamp"]}])
            )
        self.assertIn("不是启动项", str(ctx.exception))

    def test_run_rejects_unknown_device(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_config(_raw(scenes=[{"id": "x", "name": "X", "run": ["ghost"]}]))
        self.assertIn("ghost", str(ctx.exception))

    def test_duplicate_id_is_rejected(self) -> None:
        scene = {"id": "x", "name": "X", "on": ["lamp"]}
        with self.assertRaises(ValueError) as ctx:
            parse_config(_raw(scenes=[scene, dict(scene)]))
        self.assertIn("重复的场景 id", str(ctx.exception))

    def test_device_in_both_on_and_off_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_config(
                _raw(scenes=[{"id": "x", "name": "X", "on": ["lamp"], "off": ["lamp"]}])
            )
        self.assertIn("同时出现在", str(ctx.exception))

    def test_brightness_range_is_checked(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                _raw(scenes=[{"id": "x", "name": "X", "brightness": {"lamp": 200}}])
            )

    def test_round_trip_keeps_scenes(self) -> None:
        cfg = parse_config(_raw())
        dumped = hub_config_to_raw(cfg)
        again = parse_config(dumped)
        self.assertEqual(
            [hub_config_to_raw(cfg)[k] for k in ("scenes",)],
            [hub_config_to_raw(again)[k] for k in ("scenes",)],
        )
        self.assertEqual(again.scenes[0].off, ["lamp", "plug"])
        self.assertEqual(again.scenes[0].phrases, ["我要睡了", "睡觉了"])

    def test_no_scenes_key_means_empty(self) -> None:
        cfg = parse_config({"name": "study", "token": TOKEN, "devices": []})
        self.assertEqual(cfg.scenes, [])

    def test_run_round_trips(self) -> None:
        cfg = parse_config(
            _raw(
                devices=_with_steam(),
                scenes=[{"id": "work", "name": "开工", "run": ["steam"], "on": ["lamp"]}],
            )
        )
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.scenes[0].run, ["steam"])
        self.assertEqual(hub_config_to_raw(cfg)["scenes"][0]["run"], ["steam"])


class RunSceneTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = DockHub(parse_config(_raw(), Path(tmp.name) / "hub.yaml"))
        self.addCleanup(self.hub.reporter.stop)

    def test_runs_off_steps_in_order(self) -> None:
        calls: list[tuple[str, dict]] = []

        def fake(device_id, body):
            calls.append((device_id, body))
            return {"id": device_id}

        with patch.object(self.hub, "command", side_effect=fake):
            result = self.hub.run_scene("bedtime", say=False)
        self.assertEqual(calls, [("lamp", {"on": False}), ("plug", {"on": False})])
        self.assertTrue(result["ok"])
        self.assertEqual(result["id"], "bedtime")
        self.assertFalse(result["said"])

    def test_brightness_comes_with_on(self) -> None:
        hub_scenes = [
            {"id": "work", "name": "干活", "on": ["plug"], "brightness": {"lamp": 80}}
        ]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        hub = DockHub(parse_config(_raw(scenes=hub_scenes), Path(tmp.name) / "hub.yaml"))
        self.addCleanup(hub.reporter.stop)
        calls: list[tuple[str, dict]] = []

        with patch.object(hub, "command", side_effect=lambda d, b: calls.append((d, b)) or {}):
            hub.run_scene("work", say=False)
        self.assertEqual(calls, [("plug", {"on": True}), ("lamp", {"on": True, "brightness": 80})])

    def test_one_failure_does_not_stop_the_rest(self) -> None:
        def fake(device_id, body):
            if device_id == "lamp":
                raise HubError("offline", "台灯离线")
            return {"id": device_id}

        with patch.object(self.hub, "command", side_effect=fake):
            result = self.hub.run_scene("bedtime", say=False)
        self.assertFalse(result["ok"])
        self.assertEqual([item["ok"] for item in result["actions"]], [False, True])
        self.assertEqual(result["actions"][0]["code"], "offline")

    def test_unknown_scene_is_404(self) -> None:
        with self.assertRaises(HubError) as ctx:
            self.hub.run_scene("nope")
        self.assertEqual(ctx.exception.status, 404)

    def test_run_steps_come_after_lights(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        hub = DockHub(
            parse_config(
                _raw(
                    devices=_with_steam(),
                    scenes=[{"id": "work", "name": "开工", "on": ["lamp"], "run": ["steam"]}],
                ),
                Path(tmp.name) / "hub.yaml",
            )
        )
        self.addCleanup(hub.reporter.stop)
        launches: list[str] = []
        with patch.object(hub, "command"), patch.object(
            hub, "_command_action", side_effect=lambda cfg, body: launches.append(cfg.id)
        ):
            result = hub.run_scene("work", say=False)
        self.assertEqual(launches, ["steam"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["actions"][-1], {"id": "steam", "run": True, "ok": True})

    def test_run_failure_does_not_stop_lights_report(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        hub = DockHub(
            parse_config(
                _raw(
                    devices=_with_steam(),
                    scenes=[{"id": "work", "name": "开工", "off": ["lamp"], "run": ["steam"]}],
                ),
                Path(tmp.name) / "hub.yaml",
            )
        )
        self.addCleanup(hub.reporter.stop)

        def boom(cfg, body):
            raise HubError("offline", "电脑未开机")

        with patch.object(hub, "command"), patch.object(hub, "_command_action", side_effect=boom):
            result = hub.run_scene("work", say=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["actions"][0]["ok"], True)  # 灯不背锅
        run_item = result["actions"][1]
        self.assertFalse(run_item["ok"])
        self.assertEqual(run_item["code"], "offline")

    def test_voice_is_skipped_when_companion_off(self) -> None:
        with patch.object(self.hub, "command"):
            result = self.hub.run_scene("bedtime")
        self.assertFalse(result["said"])

    def test_snapshot_lists_scenes(self) -> None:
        with patch.object(
            self.hub, "_mijia_desk_slice", return_value=("login_required", None, None, [])
        ):
            snap = self.hub.snapshot()
        self.assertEqual(snap["scenes"], [{"id": "bedtime", "name": "晚安"}])

    def test_wizard_save_keeps_scenes(self) -> None:
        from dock_hub import setup_ui

        raw = setup_ui._payload_to_raw(
            {"name": "study", "token": TOKEN, "devices": _raw()["devices"]},
            self.hub.config,
        )
        self.assertEqual(raw["scenes"][0]["id"], "bedtime")
        self.assertEqual(parse_config(raw).scenes[0].say, "灯关了，早点睡。")


class SceneVoiceTest(unittest.TestCase):
    def _companion(self, runs: list) -> Companion:
        comp = Companion(CompanionConfig(enabled=True))
        comp.set_skills(
            [],
            None,
            None,
            world={
                "scene": lambda ident, say=True: runs.append((ident, say)) or {"ok": True},
                "scenes": [
                    {"id": "bedtime", "name": "晚安", "say": "灯关了，早点睡。", "phrases": ["我要睡了", "晚安"]},
                    {"id": "work", "name": "干活", "say": None, "phrases": ["开始干活"]},
                ],
            },
        )
        return comp

    def test_phrase_runs_scene_without_double_speaking(self) -> None:
        runs: list = []
        comp = self._companion(runs)
        spoken, events = comp._handle_scene("好了，我要睡了", "t1")
        self.assertEqual(spoken, "灯关了，早点睡。")
        self.assertEqual(runs, [("bedtime", False)])
        self.assertEqual(events[0]["id"], "bedtime")
        self.assertTrue(events[0]["ok"])

    def test_scene_without_say_gets_short_ack(self) -> None:
        runs: list = []
        comp = self._companion(runs)
        spoken, _ = comp._handle_scene("开始干活吧", "t2")
        self.assertEqual(spoken, "好，干活。")

    def test_unrelated_text_is_left_to_the_model(self) -> None:
        comp = self._companion([])
        self.assertIsNone(comp._handle_scene("今天天气怎么样", "t3"))

    def test_longest_phrase_wins(self) -> None:
        runs: list = []
        comp = Companion(CompanionConfig(enabled=True))
        comp.set_skills(
            [],
            None,
            None,
            world={
                "scene": lambda ident, say=True: runs.append(ident) or {"ok": True},
                "scenes": [
                    {"id": "short", "name": "睡", "phrases": ["睡"]},
                    {"id": "long", "name": "晚安", "phrases": ["我要睡了"]},
                ],
            },
        )
        comp._handle_scene("我要睡了", "t4")
        self.assertEqual(runs, ["long"])

    def test_failure_is_reported(self) -> None:
        comp = Companion(CompanionConfig(enabled=True))

        def boom(ident, say=True):
            raise HubError("offline", "台灯离线")

        comp.set_skills(
            [],
            None,
            None,
            world={"scene": boom, "scenes": [{"id": "bedtime", "name": "晚安", "phrases": ["晚安"]}]},
        )
        spoken, events = comp._handle_scene("晚安", "t5")
        self.assertEqual(spoken, "这次没动成。")
        self.assertFalse(events[0]["ok"])


class SceneHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = DockHub(parse_config(_raw(), Path(tmp.name) / "hub.yaml"))
        self.addCleanup(self.hub.reporter.stop)
        self.calls: list[tuple[str, dict]] = []
        patcher = patch.object(
            self.hub, "command", side_effect=lambda d, b: self.calls.append((d, b)) or {"id": d}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _post(self, path: str, token: str = TOKEN):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request("POST", path, body=b"{}", headers=headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8") or "{}")
        conn.close()
        return resp.status, body

    def test_scene_runs_over_http(self) -> None:
        status, body = self._post("/v1/scenes/bedtime")
        self.assertEqual(status, 200)
        self.assertEqual(body["id"], "bedtime")
        self.assertTrue(body["ok"])
        self.assertEqual(self.calls, [("lamp", {"on": False}), ("plug", {"on": False})])

    def test_unknown_scene_is_404(self) -> None:
        status, body = self._post("/v1/scenes/gone")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_needs_token(self) -> None:
        status, body = self._post("/v1/scenes/bedtime", token="")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")
