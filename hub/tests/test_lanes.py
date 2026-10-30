"""lanes.py：慢请求的名额。

两个层次都验：Lanes 本身的名额语义（满了抛、放回能再用、0 表示不限），
以及真起一个 Hub 之后 `chat` 被占满时是不是乖乖回 503 而不是排队等。

为什么值得单独测：这一层平时感觉不到，只有连续对话或者手机抽风重发时才会踩到。
踩到时如果它悄悄排队，表现是安卓一直转圈；如果是 503，手机上能立刻改口说
「她还在这句上」。
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from dock_hub.config import LimitsConfig, hub_config_to_raw, parse_config
from dock_hub.errors import HubError
from dock_hub.lanes import Lanes
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class LaneTest(unittest.TestCase):
    def test_slots_are_counted(self) -> None:
        lanes = Lanes({"chat": 2})
        with lanes.hold("chat"):
            with lanes.hold("chat"):
                self.assertEqual(lanes.depth("chat"), 2)
                with self.assertRaises(HubError) as caught:
                    with lanes.hold("chat"):
                        self.fail("第三个不该进来")
        self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(caught.exception.headers.get("Retry-After"), "2")

    def test_slot_is_freed_after_the_block(self) -> None:
        lanes = Lanes({"chat": 1})
        with lanes.hold("chat"):
            pass
        with lanes.hold("chat"):
            pass
        self.assertEqual(lanes.depth("chat"), 0)

    def test_slot_is_freed_even_when_the_body_raises(self) -> None:
        lanes = Lanes({"chat": 1})
        with self.assertRaises(RuntimeError):
            with lanes.hold("chat"):
                raise RuntimeError("模型炸了")
        with lanes.hold("chat"):
            self.assertEqual(lanes.depth("chat"), 1)

    def test_unknown_lane_is_unlimited(self) -> None:
        lanes = Lanes({"chat": 1})
        for _ in range(50):
            with lanes.hold("snapshot"):
                pass

    def test_zero_means_unlimited(self) -> None:
        lanes = Lanes({"read": 0})
        with lanes.hold("read"):
            with lanes.hold("read"):
                pass

    def test_rejections_are_counted(self) -> None:
        lanes = Lanes({"chat": 1})
        with lanes.hold("chat"):
            for _ in range(3):
                with self.assertRaises(HubError):
                    with lanes.hold("chat"):
                        pass
        self.assertEqual(lanes.rejected(), 3)

    def test_from_config(self) -> None:
        lanes = Lanes.from_config(LimitsConfig(chat_lanes=3, read_lanes=0))
        self.assertEqual(lanes.slots, {"chat": 3, "read": 0})


class LaneConfigTest(unittest.TestCase):
    BASE = {"name": "study", "token": "secret-token-value", "host": "127.0.0.1", "port": 0}

    def test_defaults(self) -> None:
        cfg = parse_config(dict(self.BASE))
        self.assertEqual(cfg.limits.chat_lanes, 2)
        self.assertEqual(cfg.limits.read_lanes, 2)

    def test_custom_survives_round_trip(self) -> None:
        cfg = parse_config({**self.BASE, "limits": {"chat_lanes": 4}})
        raw = hub_config_to_raw(cfg)
        self.assertEqual(raw["limits"]["chat_lanes"], 4)
        self.assertEqual(parse_config(raw).limits.chat_lanes, 4)

    def test_negative_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config({**self.BASE, "limits": {"chat_lanes": -1}})


class HttpBusyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
                "mini": False,
                "limits": {"chat_lanes": 1},
            }
        )
        self.hub = DockHub(cfg)
        handler = make_handler(self.hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _post(self, path: str, body: dict) -> tuple[int, dict, dict]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            path,
            body=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer secret-token-value",
                "Content-Type": "application/json",
            },
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        headers = dict(resp.getheaders())
        conn.close()
        return resp.status, payload, headers

    def test_busy_lane_answers_503(self) -> None:
        with patch.object(self.hub, "companion_chat", return_value={"reply": "嗯"}):
            with self.hub.lanes.hold("chat"):
                status, body, headers = self._post("/v1/companion/chat", {"text": "在吗"})
            self.assertEqual(status, 503)
            self.assertEqual(body["error"]["code"], "busy")
            self.assertEqual(headers.get("Retry-After"), "2")
            # 名额放开之后就能正常说话了
            status, _body, _headers = self._post("/v1/companion/chat", {"text": "在吗"})
        self.assertEqual(status, 200)

    def test_fast_endpoints_are_not_affected(self) -> None:
        with self.hub.lanes.hold("chat"):
            conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(payload["service"], "dock-hub")


if __name__ == "__main__":
    unittest.main()
