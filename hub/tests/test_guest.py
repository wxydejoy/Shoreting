"""F26 访客模式：一句话把记录收起来，一句话放回来，到点自己恢复。"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from dock_hub.config import GuestConfig, hub_config_to_raw, parse_config
from dock_hub.guest import DEFAULT_HOURS, GuestMode, clamp_hours, parse_guest
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class ParseGuestTest(unittest.TestCase):
    def test_on_phrases(self) -> None:
        for text in ("有客人", "有客人。", "来客人了", "客人来了", "来了客人", "有客人，小声点"):
            self.assertEqual(parse_guest(text), "on", text)
        for text in ("访客模式", "开访客模式", "打开访客模式", "小岸，开一下访客模式", "开访客模式吧"):
            self.assertEqual(parse_guest(text), "on", text)

    def test_off_phrases(self) -> None:
        for text in ("客人走了", "客人走了。", "没客人了", "送走客人", "关访客模式", "访客模式关掉"):
            self.assertEqual(parse_guest(text), "off", text)

    def test_chatter_is_not_a_command(self) -> None:
        for text in (
            "今天有客人来过吗",
            "家里没有客人",
            "有客人吗",
            "访客模式是什么",
            "把访客模式的事忘了",
            "今天天气怎么样",
            "",
        ):
            self.assertIsNone(parse_guest(text), text)


class ClampHoursTest(unittest.TestCase):
    def test_clamps_into_range(self) -> None:
        self.assertEqual(clamp_hours(4), 4.0)
        self.assertEqual(clamp_hours("3"), 3.0)
        self.assertEqual(clamp_hours(0), 0.5)
        self.assertEqual(clamp_hours(-1), 0.5)
        self.assertEqual(clamp_hours(999), 24.0)

    def test_broken_values_fall_back_to_default(self) -> None:
        for raw in (None, "abc", float("nan"), [6]):
            self.assertEqual(clamp_hours(raw), DEFAULT_HOURS)


class GuestModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 31, 19, 0, 0).astimezone()
        self.mode = GuestMode(now=lambda: self.now)

    def test_starts_hidden_nothing(self) -> None:
        self.assertFalse(self.mode.hiding())
        self.assertIsNone(self.mode.state())

    def test_activate_expires_and_reactivates(self) -> None:
        state = self.mode.activate(6)
        self.assertTrue(self.mode.hiding())
        self.assertEqual(state["hours"], 6.0)
        self.assertEqual(
            datetime.fromisoformat(state["until"]) - datetime.fromisoformat(state["since"]),
            timedelta(hours=6),
        )
        # 到点自动恢复：不用谁去叫
        self.now += timedelta(hours=6, seconds=1)
        self.assertFalse(self.mode.hiding())
        self.assertIsNone(self.mode.state())

    def test_reactivate_extends_instead_of_resetting(self) -> None:
        start = self.now
        self.mode.activate(6)
        self.now += timedelta(hours=5)
        state = self.mode.activate(6)
        self.assertEqual(datetime.fromisoformat(state["since"]), start)
        self.assertEqual(
            datetime.fromisoformat(state["until"]),
            start + timedelta(hours=11),
        )

    def test_deactivate(self) -> None:
        self.mode.activate(6)
        self.mode.deactivate()
        self.assertFalse(self.mode.hiding())
        self.assertIsNone(self.mode.state())

    def test_short_hours_clamped(self) -> None:
        self.assertEqual(self.mode.activate(0)["hours"], 0.5)


class GuestConfigTest(unittest.TestCase):
    def _parse(self, guest: object) -> GuestConfig:
        return parse_config(
            {"name": "study", "token": "t" * 32, "guest": guest},
            path=Path(tempfile.gettempdir()) / "hub.yaml",
        ).guest

    def test_defaults_and_switch(self) -> None:
        self.assertEqual(self._parse(None).hours, DEFAULT_HOURS)
        self.assertTrue(self._parse(None).enabled)
        self.assertTrue(self._parse(True).enabled)
        self.assertFalse(self._parse(False).enabled)

    def test_hours(self) -> None:
        self.assertEqual(self._parse({"hours": 4}).hours, 4.0)
        self.assertEqual(self._parse({"hours": 999}).hours, 24.0)
        self.assertEqual(self._parse({}).hours, DEFAULT_HOURS)
        with self.assertRaises(ValueError):
            self._parse("soon")

    def test_round_trip_only_when_not_default(self) -> None:
        cfg = parse_config(
            {"name": "study", "token": "t" * 32, "guest": {"hours": 4}},
            path=Path(tempfile.gettempdir()) / "hub.yaml",
        )
        raw = hub_config_to_raw(cfg)
        self.assertEqual(raw["guest"], {"hours": 4.0})
        cfg = parse_config(
            {"name": "study", "token": "t" * 32, "guest": False},
            path=Path(tempfile.gettempdir()) / "hub.yaml",
        )
        self.assertEqual(hub_config_to_raw(cfg)["guest"], False)
        cfg = parse_config(
            {"name": "study", "token": "t" * 32},
            path=Path(tempfile.gettempdir()) / "hub.yaml",
        )
        self.assertNotIn("guest", hub_config_to_raw(cfg))


class GuestProtocolTest(unittest.TestCase):
    """整条链路：语音开 → 记录页 404、对话不落盘 → 语音关 → 一切照旧。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "companion": {
                    "enabled": True,
                    "llm": {
                        "base_url": "http://127.0.0.1:9",
                        "model": "qwen3.5:4b",
                        "timeout_sec": 2,
                    },
                },
                "devices": [],
                "mini": False,
            },
            path=Path(self.tmp.name) / "hub.yaml",
        )
        self.hub = DockHub(cfg)
        handler = make_handler(self.hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.chat_path = Path(self.tmp.name) / "companion-chats.jsonl"

    def _request(self, method: str, path: str, body: dict | None = None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": "Bearer secret-token-value"}
        raw = None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, payload

    def _json(self, method: str, path: str, body: dict | None = None):
        status, payload = self._request(method, path, body)
        return status, json.loads(payload.decode("utf-8"))

    def test_hidden_while_guests_over(self) -> None:
        status, body = self._json("GET", "/v1/snapshot")
        self.assertEqual(status, 200)
        self.assertIsNone(body["guest"])

        status, body = self._json("POST", "/v1/companion/chat", {"text": "有客人。"})
        self.assertEqual(status, 200)
        self.assertIn("收起来", body["text"])
        status, body = self._json("GET", "/v1/snapshot")
        self.assertIsInstance(body["guest"], dict)
        self.assertTrue(self.hub.guest.hiding())

        for path in (
            "/chats",
            "/week",
            "/diary",
            "/v1/companion/chats",
            "/v1/companion/week",
            "/v1/companion/diary",
            "/v1/companion/memory",
        ):
            status, _ = self._request("GET", path)
            self.assertEqual(status, 404, path)
        status, _ = self._request("DELETE", "/v1/companion/memory/%E5%BF%8C%E5%8F%A3")
        self.assertEqual(status, 404)

        # 开关这一句本身也不落盘；跟客人的对话同样不落盘
        self._json("POST", "/v1/companion/chat", {"text": "今天吃过了"})
        self.assertFalse(self.chat_path.exists() and self.chat_path.read_text().strip())

        # 客人在场不学新东西，但她自己照常说话
        status, body = self._json("POST", "/v1/companion/chat", {"text": "记住，我不喝冰的"})
        self.assertEqual(status, 200)
        self.assertIn("先不记", body["text"])
        self.assertEqual(self.hub.companion_memory()["items"], [])

    def test_restore_after_guests_leave(self) -> None:
        self._json("POST", "/v1/companion/chat", {"text": "有客人"})
        status, body = self._json("POST", "/v1/companion/chat", {"text": "客人走了"})
        self.assertEqual(status, 200)
        self.assertIn("看得见", body["text"])
        status, body = self._json("GET", "/v1/snapshot")
        self.assertIsNone(body["guest"])
        status, body = self._json("GET", "/v1/companion/chats")
        self.assertEqual(status, 200)
        # 「客人走了」这一句是恢复之后说的，正常落盘
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["user"], "客人走了")
        # 恢复之后对话重新落盘
        self._json("POST", "/v1/companion/chat", {"text": "今天吃过了"})
        self.assertTrue(self.chat_path.exists())

    def test_disabled_by_config_leaves_chatter_alone(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "companion": {
                    "enabled": True,
                    "llm": {
                        "base_url": "http://127.0.0.1:9",
                        "model": "qwen3.5:4b",
                        "timeout_sec": 2,
                    },
                },
                "devices": [],
                "mini": False,
                "guest": False,
            },
            path=Path(self.tmp.name) / "hub.yaml",
        )
        hub = DockHub(cfg)
        handler = make_handler(hub)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.hub = hub
        self.port = server.server_address[1]
        # guest: false 时这句话不认识，落到大模型（这里没起）就 502，而不是开关
        status, _body = self._json("POST", "/v1/companion/chat", {"text": "有客人。"})
        self.assertEqual(status, 502)
        self.assertFalse(hub.guest.hiding())


if __name__ == "__main__":
    unittest.main()
