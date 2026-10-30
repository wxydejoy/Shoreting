from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dock_hub.config import parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class LongPollTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        marker = root / "steam.exe"
        marker.write_text("fake", encoding="utf-8")
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [
                    {
                        "id": "steam",
                        "name": "Steam",
                        "type": "action",
                        "icon": "steam",
                        "path": str(marker),
                    }
                ],
                "mini": False,
            }
        )
        self.hub = DockHub(cfg)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _get(self, path: str):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            started = time.monotonic()
            conn.request("GET", path, headers={"Authorization": "Bearer secret-token-value"})
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body, time.monotonic() - started
        finally:
            conn.close()

    def test_snapshot_carries_seq(self) -> None:
        status, body, _ = self._get("/v1/snapshot")
        self.assertEqual(status, 200)
        self.assertIsInstance(body["seq"], int)
        self.assertGreaterEqual(body["seq"], 0)

    def test_plain_poll_returns_immediately(self) -> None:
        _, first, _ = self._get("/v1/snapshot")
        _, second, elapsed = self._get(f"/v1/snapshot?since={first['seq']}")
        self.assertLess(elapsed, 1.0)
        self.assertEqual(second["seq"], first["seq"])

    def test_wait_times_out_with_same_seq(self) -> None:
        _, first, _ = self._get("/v1/snapshot")
        _, second, elapsed = self._get(f"/v1/snapshot?since={first['seq']}&wait=0.3")
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 3.0)
        self.assertEqual(second["seq"], first["seq"])

    def test_change_wakes_the_waiting_request(self) -> None:
        _, first, _ = self._get("/v1/snapshot")
        timer = threading.Timer(0.15, self.hub.note_change)
        timer.start()
        self.addCleanup(timer.cancel)
        _, second, elapsed = self._get(f"/v1/snapshot?since={first['seq']}&wait=5")
        self.assertLess(elapsed, 2.0)
        self.assertGreater(second["seq"], first["seq"])

    def test_command_bumps_seq(self) -> None:
        _, first, _ = self._get("/v1/snapshot")
        with patch("dock_hub.service.launch"):
            status, _payload = self._post("/v1/devices/steam/command", {"run": True})
        self.assertEqual(status, 200)
        _, second, _ = self._get("/v1/snapshot")
        self.assertGreater(second["seq"], first["seq"])

    def test_garbage_since_falls_back_to_plain_poll(self) -> None:
        status, body, elapsed = self._get("/v1/snapshot?since=abc&wait=5")
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 1.0)
        self.assertIn("seq", body)

    def test_wait_is_capped(self) -> None:
        _, first, _ = self._get("/v1/snapshot")
        with patch("dock_hub.server.MAX_WAIT", 0.2):
            _, _second, elapsed = self._get(f"/v1/snapshot?since={first['seq']}&wait=999")
        self.assertLess(elapsed, 2.0)

    def test_requires_token(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/v1/snapshot?since=1&wait=5")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 401)
        finally:
            conn.close()

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(
                "POST",
                path,
                body=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": "Bearer secret-token-value",
                    "Content-Type": "application/json",
                },
            )
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()


class WaitChangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {"name": "study", "token": "t", "host": "127.0.0.1", "port": 0, "mini": False}
        )
        self.hub = DockHub(cfg)

    def test_zero_timeout_returns_immediately(self) -> None:
        self.assertFalse(self.hub.wait_change(self.hub.snapshot_seq(), 0))

    def test_stale_start_returns_true(self) -> None:
        seq = self.hub.snapshot_seq()
        self.hub.note_change()
        self.assertTrue(self.hub.wait_change(seq, 0.5))

    def test_wakes_on_change(self) -> None:
        seq = self.hub.snapshot_seq()
        timer = threading.Timer(0.1, self.hub.note_change)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        self.assertTrue(self.hub.wait_change(seq, 5))
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
