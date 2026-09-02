from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from helm_mini.sampler import MiniSampler
from helm_mini.server import make_handler
from helm_mini.telemetry import TelemetryStore


class TelemetryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = TelemetryStore(self.root)

    def test_ingest_updates_latest_without_jsonl(self) -> None:
        mini_pc = {"online": True, "cpu": {"percent": 3}}
        latest = self.store.ingest_windows(
            {"pc": {"online": True, "cpu": {"percent": 12}}},
            mini_pc,
        )
        self.assertEqual(latest["windows"]["pc"]["cpu"]["percent"], 12)
        self.assertEqual(latest["mini"]["pc"]["cpu"]["percent"], 3)
        self.assertTrue((self.root / "desk-state.json").is_file())
        self.assertFalse((self.root / "log").exists())

    def test_touch_mini_writes_jsonl_and_prunes(self) -> None:
        log = self.root / "log"
        log.mkdir()
        old = log / "desk-2020-01-01.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        self.store.touch_mini({"online": True, "cpu": {"percent": 4}}, persist_log=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertTrue((log / f"desk-{day}.jsonl").is_file())
        self.assertFalse(old.is_file())
        self.assertEqual(self.store.latest()["mini"]["pc"]["cpu"]["percent"], 4)


class TelemetryHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = TelemetryStore(Path(self.tmp.name))
        self.sampler = MiniSampler(sample_ms=50_000)
        self.sampler._sample = {
            "online": True,
            "cpu": {"percent": 3.0},
            "memory": {"percent": 20.0},
            "updated_at": "2026-09-02T00:00:00Z",
        }
        handler = make_handler(self.sampler, "secret", "mini", self.store)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _request(self, method: str, path: str, body: dict | None = None, token: str = "secret"):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        raw = None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, payload

    def test_snapshot_shape_unchanged(self) -> None:
        status, body = self._request("GET", "/v1/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "helm-mini")
        self.assertEqual(set(body), {"protocol", "service", "pc"})
        self.assertEqual(body["pc"]["cpu"]["percent"], 3.0)

    def test_telemetry_roundtrip(self) -> None:
        status, body = self._request(
            "POST",
            "/v1/telemetry",
            {"pc": {"online": True, "cpu": {"percent": 88}}, "temperature": {"celsius": 24.0}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["desk"]["windows"]["pc"]["cpu"]["percent"], 88)
        self.assertEqual(body["desk"]["mini"]["pc"]["cpu"]["percent"], 3.0)
        status, desk = self._request("GET", "/v1/desk")
        self.assertEqual(status, 200)
        self.assertEqual(desk["desk"]["windows"]["temperature"]["celsius"], 24.0)

    def test_telemetry_requires_token(self) -> None:
        status, body = self._request("POST", "/v1/telemetry", {}, token="")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")


if __name__ == "__main__":
    unittest.main()
