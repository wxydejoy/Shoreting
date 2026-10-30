from __future__ import annotations

import gzip
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from dock_hub.config import parse_config
from dock_hub.server import GZIP_MIN, make_handler
from dock_hub.service import DockHub


class GzipTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        marker = Path(tmp.name) / "steam.exe"
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
                        "id": "code",
                        "name": "Visual Studio Code 编辑器",
                        "type": "action",
                        "icon": "code",
                        "path": str(marker),
                    },
                    {
                        "id": "browser",
                        "name": "Chrome 浏览器（工作用）",
                        "type": "action",
                        "icon": "chrome",
                        "path": str(marker),
                    },
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

    def _get(self, path: str, headers: dict[str, str] | None = None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def test_snapshot_gzip_when_accepted(self) -> None:
        status, headers, body = self._get(
            "/v1/snapshot",
            {"Authorization": "Bearer secret-token-value", "Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")
        self.assertGreater(int(headers["Content-Length"]), 0)
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        self.assertEqual(payload["protocol"], 1)

    def test_snapshot_plain_without_accept_encoding(self) -> None:
        status, headers, body = self._get(
            "/v1/snapshot",
            {"Authorization": "Bearer secret-token-value", "Accept-Encoding": "identity"},
        )
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertEqual(json.loads(body.decode("utf-8"))["protocol"], 1)

    def test_small_error_body_is_not_compressed(self) -> None:
        status, headers, body = self._get(
            "/v1/snapshot",
            {"Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 401)
        self.assertLess(len(body), GZIP_MIN)
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertEqual(json.loads(body.decode("utf-8"))["error"]["code"], "unauthorized")

    def test_page_html_is_compressed(self) -> None:
        status, headers, body = self._get("/chats", {"Accept-Encoding": "gzip, deflate"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertIn(b"<", gzip.decompress(body))

    def test_page_keeps_no_store(self) -> None:
        _, headers, _ = self._get("/chats", {"Accept-Encoding": "gzip"})
        self.assertEqual(headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
