"""在桌上报（F24）：摄像头本地检测有人，回来时她说一句。

只测 Hub 侧的规矩：away → present 且离座够长才开口、短离座不问候、
present → away 不说话、配额与免打扰拦下就不说（不补发）、参数校验、
snapshot 带状态。安卓侧的去抖（人脸检测、5 分钟计时）不在这里。
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from dock_hub.config import PresenceConfig, ProactiveConfig, parse_config, proactive_to_raw
from dock_hub.errors import HubError
from dock_hub.server import make_handler
from dock_hub.service import DockHub

TOKEN = "secret-token-value"


def _raw(**extra):
    body = {
        "name": "study",
        "token": TOKEN,
        "host": "127.0.0.1",
        "port": 0,
        "devices": [
            {"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯", "on_prop": "on"},
        ],
        "companion": {"enabled": True},
    }
    body.update(extra)
    return body


def _hub(raw=None) -> tuple[DockHub, list[str], tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    hub = DockHub(parse_config(raw or _raw(), Path(tmp.name) / "hub.yaml"))
    said: list[str] = []
    hub.proactive._speak = lambda text: (said.append(text), True)[1]
    return hub, said, tmp


def _enabled(**extra) -> ProactiveConfig:
    # 免打扰时段清空：夜里跑测试时别让 23:00 的闸把断言拦了
    extra.setdefault("daily_limit", 6)
    return ProactiveConfig(enabled=True, quiet=(), **extra)


class DeskStateTest(unittest.TestCase):
    """状态机：离座满 5 分钟的回座才值得开口。"""

    def test_first_present_does_not_greet(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        result = hub.report_desk(True, 600)
        self.assertEqual(said, [])  # Hub 刚重启的第一报不问候
        self.assertIsNone(result["said"])

    def test_short_gap_is_not_greeted(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        hub.report_desk(True, 0)
        hub.report_desk(False)
        result = hub.report_desk(True, 299)  # 差一秒都不算离座
        self.assertEqual(said, [])
        self.assertIsNone(result["said"])

    def test_long_gap_greets_once(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        hub.report_desk(True, 0)
        hub.report_desk(False)
        result = hub.report_desk(True, 312)
        self.assertEqual(said, ["回来了。"])
        self.assertEqual(result["said"], "回来了。")
        self.assertEqual(result["away_sec"], 312)

    def test_repeated_same_state_is_silent(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        hub.report_desk(True, 0)
        hub.report_desk(False)
        hub.report_desk(False)  # 幂等：同状态重复上报不算变化
        hub.report_desk(True, 600)
        self.assertEqual(len(said), 1)
        hub.report_desk(True, 610)
        self.assertEqual(len(said), 1)

    def test_leaving_never_speaks(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        hub.report_desk(True, 0)
        result = hub.report_desk(False)
        self.assertEqual(said, [])
        self.assertIsNone(result["said"])

    def test_desk_text_override_and_off(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled(presence=PresenceConfig(desk_text="坐回来了，喝水。")))
        hub.report_desk(True, 0)
        hub.report_desk(False)
        hub.report_desk(True, 900)
        self.assertEqual(said, ["坐回来了，喝水。"])
        hub.proactive.configure(_enabled(presence=PresenceConfig(desk_text="")))
        hub.report_desk(False)
        hub.report_desk(True, 900)
        self.assertEqual(len(said), 1)  # desk_text 置空：只记状态不说话


class DeskGatesTest(unittest.TestCase):
    """问候也要守主动播报的三道闸：开关、免打扰、每日配额。"""

    def test_silent_when_proactive_off(self) -> None:
        hub, said, tmp = _hub()  # proactive 默认 enabled=False
        self.addCleanup(tmp.cleanup)
        hub.report_desk(True, 0)
        result = hub.report_desk(False)
        result = hub.report_desk(True, 3600)
        self.assertEqual(said, [])
        self.assertIsNone(result["said"])

    def test_quiet_hours_block(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        now = datetime.now().astimezone()
        hub.proactive.configure(
            ProactiveConfig(
                enabled=True,
                daily_limit=6,
                quiet=(now.strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M")),
            )
        )
        hub.report_desk(True, 0)
        hub.report_desk(False)
        result = hub.report_desk(True, 3600, now)
        self.assertEqual(said, [])
        self.assertIsNone(result["said"])

    def test_daily_quota_block(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled(daily_limit=1))
        hub.proactive.desk("先说句别的。")  # 配额先被别的话用掉
        hub.report_desk(True, 0)
        hub.report_desk(False)
        result = hub.report_desk(True, 3600)
        self.assertIsNone(result["said"])
        self.assertEqual(len(said), 1)


class DeskValidationTest(unittest.TestCase):
    """参数面：present 必须布尔，away_sec 必须非负整数。"""

    def _desk(self) -> tuple[DockHub, tempfile.TemporaryDirectory]:
        hub, _, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        return hub, tmp

    def test_present_must_be_bool(self) -> None:
        hub, _ = self._desk()
        for bad in (1, "yes", None):
            with self.assertRaises(HubError):
                hub.report_desk(bad)

    def test_away_sec_must_be_nonnegative_int(self) -> None:
        hub, _ = self._desk()
        for bad in (-1, 1.5, "300", True):
            with self.assertRaises(HubError):
                hub.report_desk(True, bad)
        hub.report_desk(True, 0)  # 0 合法
        hub.report_desk(True, None)  # 缺省合法


class DeskSnapshotTest(unittest.TestCase):
    """snapshot 带出在桌状态，安卓好画、周报好算。"""

    def test_null_until_first_report(self) -> None:
        hub, _, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(hub.snapshot()["at_desk"], {"state": None, "since": None})

    def test_state_and_since_are_reported(self) -> None:
        hub, _, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        now = datetime(2026, 10, 30, 19, 30, 0).astimezone()
        hub.report_desk(True, 0, now)
        body = hub.snapshot()["at_desk"]
        self.assertEqual(body["state"], "present")
        self.assertEqual(body["since"], now.isoformat(timespec="seconds"))
        hub.report_desk(False)
        self.assertEqual(hub.snapshot()["at_desk"]["state"], "away")


class DeskConfigTest(unittest.TestCase):
    """配置往返：desk_text 默认值、自定义、presence: false 双双置空。"""

    def test_parse_default_and_custom(self) -> None:
        cfg = parse_config(_raw(proactive={"enabled": True}))
        self.assertEqual(cfg.proactive.presence.desk_text, "回来了。")
        cfg = parse_config(
            _raw(proactive={"enabled": True, "presence": {"desk_text": "坐回来了。"}})
        )
        self.assertEqual(cfg.proactive.presence.desk_text, "坐回来了。")
        self.assertEqual(cfg.proactive.presence.home_text, "回来了。")  # 各管各的

    def test_parse_off(self) -> None:
        cfg = parse_config(_raw(proactive={"enabled": True, "presence": False}))
        self.assertEqual(cfg.proactive.presence.desk_text, "")
        self.assertEqual(cfg.proactive.presence.home_text, "")

    def test_round_trip(self) -> None:
        cfg = parse_config(
            _raw(proactive={"enabled": True, "presence": {"desk_text": ""}})
        )
        raw = proactive_to_raw(cfg.proactive)
        self.assertEqual(raw["presence"], {"desk_text": ""})
        # 默认值不落盘
        default = parse_config(_raw(proactive={"enabled": True}))
        self.assertNotIn("presence", proactive_to_raw(default.proactive))


class DeskRouteTest(unittest.TestCase):
    """HTTP 面：鉴权、参数校验、回座问候。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(_raw(), Path(self.tmp.name) / "hub.yaml")
        hub = DockHub(cfg)
        self.addCleanup(hub.proactive.configure, ProactiveConfig())
        hub.proactive.configure(_enabled())
        said: list[str] = []
        hub.proactive._speak = lambda text: (said.append(text), True)[1]
        self.said = said
        self.hub = hub
        handler = make_handler(hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)

    def _post(self, body: dict, *, token: str | None = TOKEN) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            conn.request("POST", "/v1/at-desk", json.dumps(body), headers)
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            resp.close()
            return resp.status, payload
        finally:
            conn.close()

    def test_needs_auth(self) -> None:
        status, _ = self._post({"present": True}, token=None)
        self.assertEqual(status, 401)

    def test_round(self) -> None:
        status, body = self._post({"present": True})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "present": True, "away_sec": None, "said": None})
        status, body = self._post({"present": False})
        self.assertEqual(status, 200)
        status, body = self._post({"present": True, "away_sec": 400})
        self.assertEqual(status, 200)
        self.assertEqual(body["said"], "回来了。")
        self.assertEqual(self.said, ["回来了。"])

    def test_bad_present_is_400(self) -> None:
        status, body = self._post({"present": "yes"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_bad_away_sec_is_400(self) -> None:
        status, _ = self._post({"present": True, "away_sec": -5})
        self.assertEqual(status, 400)
        status, _ = self._post({"present": True, "away_sec": "300"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
