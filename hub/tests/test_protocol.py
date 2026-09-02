from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dock_hub.config import parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class ProtocolTest(unittest.TestCase):
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
                    },
                    {
                        "id": "missing-app",
                        "name": "缺失",
                        "type": "action",
                        "path": str(root / "gone.exe"),
                    },
                ],
                "mini": False,
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

    def _request(self, method: str, path: str, body: dict | None = None, token: str | None = "secret-token-value"):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Accept": "application/json"}
        raw = None
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, payload

    def test_health_no_auth(self) -> None:
        status, body = self._request("GET", "/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "dock-hub")
        self.assertEqual(body["protocol"], 1)
        self.assertEqual(body["name"], "study")
        self.assertTrue(body["ok"])

    def test_snapshot_unauthorized(self) -> None:
        status, body = self._request("GET", "/v1/snapshot", token=None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_snapshot_keeps_actions_when_logged_out(self) -> None:
        status, body = self._request("GET", "/v1/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(body["hub"]["mijia"], "login_required")
        self.assertIsNone(body["temperature"])
        ids = [d["id"] for d in body["devices"]]
        self.assertEqual(ids, ["steam", "missing-app"])
        steam = body["devices"][0]
        self.assertEqual(steam["type"], "action")
        self.assertTrue(steam["online"])
        self.assertEqual(steam["icon"], "steam")
        self.assertFalse(steam["on"])
        self.assertFalse(body["devices"][1]["online"])
        self.assertIn("pc", body)
        self.assertIsNone(body["pc"])
        self.assertIn("media", body)
        self.assertIsNotNone(body["media"])
        self.assertTrue(body["media"]["online"])
        self.assertIn("playing", body["media"])
        self.assertIn("companion", body)
        self.assertIsNone(body["companion"])

    def test_action_run(self) -> None:
        with patch("dock_hub.service.launch") as mocked:
            status, body = self._request("POST", "/v1/devices/steam/command", {"run": True})
            self.assertEqual(status, 200)
            self.assertEqual(body["id"], "steam")
            self.assertEqual(body["type"], "action")
            self.assertIn("last_run_at", body)
            mocked.assert_called_once()

    def test_action_missing_is_offline(self) -> None:
        status, body = self._request("POST", "/v1/devices/missing-app/command", {"run": True})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "offline")

    def test_unknown_device(self) -> None:
        status, body = self._request("POST", "/v1/devices/nope/command", {"on": True})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_empty_command(self) -> None:
        status, body = self._request("POST", "/v1/devices/steam/command", {})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_action_rejects_on(self) -> None:
        status, body = self._request("POST", "/v1/devices/steam/command", {"on": True})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "unsupported")

    def test_action_run_false_unsupported(self) -> None:
        status, body = self._request("POST", "/v1/devices/steam/command", {"run": False})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "unsupported")

    def test_media_toggle(self) -> None:
        status, body = self._request("POST", "/v1/devices/media/command", {"media": "toggle"})
        self.assertEqual(status, 200)
        self.assertTrue(body["online"])
        self.assertTrue(body["playing"])
        status, body = self._request("POST", "/v1/devices/media/command", {"media": "toggle"})
        self.assertEqual(status, 200)
        self.assertFalse(body["playing"])

    def test_media_rejects_on(self) -> None:
        status, body = self._request("POST", "/v1/devices/media/command", {"on": True})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "unsupported")

    def test_action_rejects_media(self) -> None:
        status, body = self._request("POST", "/v1/devices/steam/command", {"media": "toggle"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "unsupported")

    def test_companion_chats_page(self) -> None:
        status, body = self._request("GET", "/v1/companion/chats", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["items"], [])
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/chats")
        resp = conn.getresponse()
        html = resp.read().decode("utf-8")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.getheader("Content-Type") or "")
        self.assertIn("守岸人", html)
        conn.close()

    def test_companion_disabled_is_404(self) -> None:
        status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        status, body = self._request("GET", "/v1/companion/audio/abc")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        status, body = self._request("POST", "/v1/companion/stop", {})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        status, body = self._request("POST", "/v1/companion/announce", {"text": "灯关了。"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")


class ConfigTest(unittest.TestCase):
    def test_rejects_placeholder_token(self) -> None:
        with self.assertRaises(ValueError):
            parse_config({"name": "x", "token": "replace-with-a-long-random-string", "devices": []})

    def test_yaml_on_becomes_prop_name(self) -> None:
        import yaml

        raw = yaml.safe_load(
            """
name: x
token: ok-token
devices:
  - id: lamp
    name: 灯
    type: light
    mijia_name: 灯
    on_prop: on
"""
        )
        cfg = parse_config(raw)
        self.assertEqual(cfg.devices[0].on_prop, "on")

    def test_action_requires_path(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                {
                    "name": "x",
                    "token": "ok-token",
                    "devices": [{"id": "a", "name": "A", "type": "action"}],
                }
            )

    def test_rejects_reserved_media_id(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                {
                    "name": "x",
                    "token": "ok-token",
                    "devices": [
                        {"id": "media", "name": "媒体", "type": "action", "path": "/bin/true"}
                    ],
                }
            )

    def test_media_disabled_is_null(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "media": {"enabled": False},
                "devices": [],
                "mini": False,
            }
        )
        hub = DockHub(cfg)
        snap = hub.snapshot()
        self.assertIn("media", snap)
        self.assertIsNone(snap["media"])

    def test_pc_enabled_in_snapshot(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "pc": {"enabled": True, "sample_ms": 1000, "gpu": False, "cpu_temp": False},
                "devices": [],
                "mini": False,
            }
        )
        hub = DockHub(cfg)
        snap = hub.snapshot()
        self.assertIsNotNone(snap["pc"])
        self.assertIn("online", snap["pc"])
        self.assertIn("updated_at", snap["pc"])
        if snap["pc"]["online"]:
            self.assertIn("percent", snap["pc"]["cpu"])
            self.assertIn("percent", snap["pc"]["memory"])
            self.assertNotIn("gpu", snap["pc"])


if __name__ == "__main__":
    unittest.main()
