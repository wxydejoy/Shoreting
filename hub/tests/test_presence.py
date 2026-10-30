"""在场上报（F25）：手机连上家里 WiFi，她说一句「回来了」。

只测 Hub 侧的规矩：away → home 才开口、unknown → home 不开口、
配额与免打扰拦下就不说（不补发）、state 校验、snapshot 带状态。
安卓侧的去抖（连上才算 home，断开满 10 分钟才算 away）不在这里。
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
from unittest.mock import patch

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


class PresenceStateTest(unittest.TestCase):
    """状态机：只有 away → home 那个边沿值得开口。"""

    def test_bad_state_rejected(self) -> None:
        hub, _, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(HubError):
            hub.report_presence("sleeping")
        with self.assertRaises(HubError):
            hub.report_presence("")
        self.assertIsNone(hub._presence)

    def test_first_home_does_not_greet(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        result = hub.report_presence("home")
        self.assertEqual(said, [])  # Hub 刚重启的第一报不问候
        self.assertIsNone(result["previous"])
        self.assertIsNone(result["said"])

    def test_away_to_home_greets_once(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        hub.report_presence("home")
        hub.report_presence("away")
        result = hub.report_presence("home")
        self.assertEqual(said, ["回来了。"])
        self.assertEqual(result["said"], "回来了。")
        self.assertEqual(result["previous"], "away")

    def test_repeated_same_state_is_silent(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled())
        hub.report_presence("home")
        hub.report_presence("home")  # 幂等：同状态重复上报不算变化
        hub.report_presence("away")
        hub.report_presence("away")
        self.assertEqual(said, [])
        result = hub.report_presence("home")
        self.assertEqual(said, ["回来了。"])
        self.assertEqual(result["said"], "回来了。")

    def test_custom_text_and_disabled_text(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled(presence=PresenceConfig(home_text="回来啦，饭在锅里。")))
        hub.report_presence("away")
        hub.report_presence("home")
        self.assertEqual(said, ["回来啦，饭在锅里。"])

    def test_empty_text_means_no_greeting(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled(presence=PresenceConfig(home_text="")))
        hub.report_presence("away")
        hub.report_presence("home")
        self.assertEqual(said, [])


class PresenceGatesTest(unittest.TestCase):
    """问候也要守主动播报的三道闸：开关、免打扰、每日配额。"""

    def test_silent_when_proactive_off(self) -> None:
        hub, said, tmp = _hub()  # proactive 默认 enabled=False
        self.addCleanup(tmp.cleanup)
        hub.report_presence("away")
        result = hub.report_presence("home")
        self.assertEqual(said, [])
        self.assertIsNone(result["said"])

    def test_quiet_hours_block(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        now = datetime.now().astimezone()
        hub.proactive.configure(ProactiveConfig(enabled=True, daily_limit=6, quiet=(now.strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M"))))
        hub.report_presence("away")
        result = hub.report_presence("home", now)
        self.assertEqual(said, [])
        self.assertIsNone(result["said"])

    def test_daily_quota_block(self) -> None:
        hub, said, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        hub.proactive.configure(_enabled(daily_limit=1))
        hub.proactive.presence("早上好。")  # 配额先被别的话用掉
        self.assertEqual(said, ["早上好。"])
        hub.report_presence("away")
        result = hub.report_presence("home")
        self.assertIsNone(result["said"])
        self.assertEqual(len(said), 1)


class PresenceSnapshotTest(unittest.TestCase):
    """snapshot 带出在场状态，安卓好画「在 / 待机」。"""

    def test_null_until_first_report(self) -> None:
        hub, _, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(hub.snapshot()["presence"], {"state": None, "since": None})

    def test_state_and_since_are_reported(self) -> None:
        hub, _, tmp = _hub()
        self.addCleanup(tmp.cleanup)
        now = datetime(2026, 10, 30, 19, 30, 0).astimezone()
        hub.report_presence("home", now)
        body = hub.snapshot()["presence"]
        self.assertEqual(body["state"], "home")
        self.assertEqual(body["since"], now.isoformat(timespec="seconds"))
        hub.report_presence("away")
        self.assertEqual(hub.snapshot()["presence"]["state"], "away")


class PresenceConfigTest(unittest.TestCase):
    """配置往返：默认一句话、自定义话、presence: false。"""

    def test_parse_default_and_custom(self) -> None:
        cfg = parse_config(_raw(proactive={"enabled": True}))
        self.assertEqual(cfg.proactive.presence.home_text, "回来了。")
        cfg = parse_config(_raw(proactive={"enabled": True, "presence": {"home_text": "回来啦。"}}))
        self.assertEqual(cfg.proactive.presence.home_text, "回来啦。")

    def test_parse_off(self) -> None:
        cfg = parse_config(_raw(proactive={"enabled": True, "presence": False}))
        self.assertEqual(cfg.proactive.presence.home_text, "")
        with self.assertRaises(ValueError):
            parse_config(_raw(proactive={"enabled": True, "presence": "hi"}))

    def test_round_trip(self) -> None:
        cfg = parse_config(_raw(proactive={"enabled": True, "presence": {"home_text": "回来啦。"}}))
        raw = proactive_to_raw(cfg.proactive)
        self.assertEqual(raw["presence"], {"home_text": "回来啦。"})
        # 默认话不落盘，免得示例文件越来越长
        default = parse_config(_raw(proactive={"enabled": True}))
        self.assertNotIn("presence", proactive_to_raw(default.proactive))


class PresenceRouteTest(unittest.TestCase):
    """HTTP 面：鉴权、参数校验、幂等。"""

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
            conn.request("POST", "/v1/presence", json.dumps(body), headers)
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            resp.close()
            return resp.status, payload
        finally:
            conn.close()

    def test_needs_auth(self) -> None:
        status, _ = self._post({"state": "home"}, token=None)
        self.assertEqual(status, 401)

    def test_round(self) -> None:
        status, body = self._post({"state": "home"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "state": "home", "previous": None, "said": None})
        status, body = self._post({"state": "away"})
        self.assertEqual(status, 200)
        status, body = self._post({"state": "home"})
        self.assertEqual(status, 200)
        self.assertEqual(body["said"], "回来了。")
        self.assertEqual(self.said, ["回来了。"])

    def test_bad_state_is_400(self) -> None:
        status, body = self._post({"state": "vacation"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")
