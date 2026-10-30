"""离家总控（F18）：能关的全关，报告没关掉的。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dock_hub.config import parse_config
from dock_hub.errors import HubError
from dock_hub.service import DockHub

TOKEN = "secret-token-value"


def _raw(**extra):
    body = {
        "name": "study",
        "token": TOKEN,
        "host": "127.0.0.1",
        "port": 0,
        "devices": [
            {"id": "ceiling", "name": "吸顶灯", "type": "light", "mijia_name": "吸顶灯", "on_prop": "on"},
            {"id": "plug", "name": "显示器插座", "type": "switch", "mijia_name": "显示器插座", "on_prop": "on"},
            {"id": "strip", "name": "灯带", "type": "ws2812", "base_url": "http://127.0.0.1:17930"},
            {"id": "steam", "name": "Steam", "type": "action", "program": "/bin/echo"},
            {"id": "thermo", "name": "温湿度计", "type": "sensor", "mijia_name": "温湿度计"},
        ],
        "companion": {"enabled": True},
    }
    body.update(extra)
    return body


def _hub(raw=None):
    tmp = tempfile.TemporaryDirectory()
    hub = DockHub(parse_config(raw or _raw(), Path(tmp.name) / "hub.yaml"))
    hub.__enter__ = lambda self: hub  # pragma: no cover - 不用 with
    return hub, tmp


class LeavingTest(unittest.TestCase):
    def test_switchable_types(self) -> None:
        cfg = parse_config(_raw())
        kinds = {d.id: d.is_switchable for d in cfg.devices}
        self.assertTrue(kinds["ceiling"])
        self.assertTrue(kinds["plug"])
        self.assertTrue(kinds["strip"])
        self.assertFalse(kinds["steam"])  # 启动项是「开」的东西
        self.assertFalse(kinds["thermo"])  # 传感器没有开关

    def _hub_with(self, *, fail: set[str] | None = None, calls: list[str] | None = None):
        hub, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        fail = fail or set()

        def fake_command(device_id, body):
            if calls is not None:
                calls.append(device_id)
            if device_id in fail:
                raise HubError("offline", "米家设备离线")
            return {"ok": True}

        with patch.object(hub, "command", side_effect=fake_command):
            yield hub

    def test_all_off_reported_clean(self) -> None:
        calls: list[str] = []
        for hub in self._hub_with(calls=calls):
            result = hub.leave_home()
        self.assertEqual(calls, ["ceiling", "plug", "strip"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["left"], [])
        self.assertEqual(result["spoken"], "都关好了，3 个，路上小心。")

    def test_failure_is_reported(self) -> None:
        for hub in self._hub_with(fail={"plug"}):
            result = hub.leave_home()
        self.assertFalse(result["ok"])
        self.assertEqual(result["left"], ["plug"])
        self.assertEqual(result["spoken"], "显示器插座没关上，其他的都关了。")

    def test_all_failure(self) -> None:
        for hub in self._hub_with(fail={"ceiling", "plug", "strip"}):
            result = hub.leave_home()
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["left"]), 3)
        self.assertIn("一个都没关成", result["spoken"])

    def test_nothing_switchable(self) -> None:
        raw = _raw(
            devices=[
                {"id": "steam", "name": "Steam", "type": "action", "program": "/bin/echo"},
                {"id": "thermo", "name": "温湿度计", "type": "sensor", "mijia_name": "温"},
            ]
        )
        hub, tmp = _hub(raw)
        self.addCleanup(tmp.cleanup)
        result = hub.leave_home()
        self.assertFalse(result["ok"])
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["spoken"], "家里没有归我管的灯和插座。")

    def test_at_most_two_names_in_speech(self) -> None:
        raw = _raw(
            devices=[
                {"id": f"lamp{i}", "name": f"灯{i}", "type": "light", "mijia_name": f"灯{i}", "on_prop": "on"}
                for i in range(3)
            ]
        )
        hub, tmp = _hub(raw)
        self.addCleanup(tmp.cleanup)
        with patch.object(hub, "command", side_effect=HubError("offline", "离线")):
            result = hub.leave_home()
        self.assertEqual(len(result["left"]), 3)
        # 全没关成走另一句，但名单同样只点两名
        self.assertEqual(result["spoken"], "一个都没关成，灯0、灯1还开着，回头看看。")

    def test_world_gets_leave(self) -> None:
        hub, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub._wire_companion()
        self.assertTrue(callable(hub.companion._world.get("leave")))


class LeavingVoiceTest(unittest.TestCase):
    """语音侧：她说那句话当回复。"""

    def _companion(self):
        from dock_hub.companion import Companion
        from dock_hub.config import CompanionConfig

        return Companion(CompanionConfig(enabled=True))

    def test_phrases(self) -> None:
        from dock_hub.companion import parse_leaving

        for text in ("我出门了", "出门了", "我要出门啦", "我出去了", "我外出咯", "这就走了"):
            self.assertTrue(parse_leaving(text), text)
        for text in ("别走了", "出门口有快递", "几点了"):
            self.assertFalse(parse_leaving(text), text)

    def test_speaks_the_report(self) -> None:
        from dock_hub.companion import parse_leaving

        companion = self._companion()
        companion._world["leave"] = lambda: {
            "ok": False,
            "left": ["desk-lamp"],
            "spoken": "台灯没关上，其他的都关了。",
        }
        got = companion._handle_leaving("我出门了", "t1")
        self.assertIsNotNone(got)
        spoken, events = got
        self.assertEqual(spoken, "台灯没关上，其他的都关了。")
        self.assertEqual(events[0]["id"], "leave")

    def test_no_hub_no_trigger(self) -> None:
        companion = self._companion()
        self.assertIsNone(companion._handle_leaving("我出门了", "t1"))

    def test_hub_error(self) -> None:
        from dock_hub.errors import HubError

        companion = self._companion()

        def boom():
            raise HubError("offline", "米家没登录")

        companion._world["leave"] = boom
        spoken, events = companion._handle_leaving("我出门了", "t1")
        self.assertEqual(spoken, "这次没动成。")
        self.assertFalse(events[0]["ok"])

    def test_action_line(self) -> None:
        from dock_hub.companion import parse_leave_action

        self.assertTrue(parse_leave_action("ACTION: home.leave"))
        self.assertFalse(parse_leave_action("ACTION: timer.25"))


if __name__ == "__main__":
    unittest.main()
