from __future__ import annotations

import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dock_hub.assets import clear_cache, etag_for, read_asset
from dock_hub.config import parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class ReadAssetTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "shell.css"
        self.path.write_text("body{color:#fff}", encoding="utf-8")

    def test_second_read_hits_cache(self) -> None:
        first = read_asset(self.path)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("不该再读盘")):
            second = read_asset(self.path)
        self.assertEqual(first, second)
        self.assertIs(first, second)

    def test_rewrite_invalidates(self) -> None:
        first = read_asset(self.path)
        self.path.write_text("body{color:#000}", encoding="utf-8")
        os.utime(self.path, ns=(self.path.stat().st_atime_ns, self.path.stat().st_mtime_ns + 1_000_000))
        second = read_asset(self.path)
        self.assertNotEqual(first, second)
        self.assertEqual(second, b"body{color:#000}")

    def test_missing_file_still_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_asset(self.path.with_name("nope.css"))

    def test_etag_tracks_content(self) -> None:
        self.assertEqual(etag_for(b"a"), etag_for(b"a"))
        self.assertNotEqual(etag_for(b"a"), etag_for(b"b"))
        self.assertTrue(etag_for(b"a").startswith('"'))


class AssetHeaderTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
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

    def test_asset_is_cacheable(self) -> None:
        status, headers, body = self._get("/assets/shell.css")
        self.assertEqual(status, 200)
        self.assertIn("max-age", headers.get("Cache-Control", ""))
        self.assertTrue(headers.get("ETag"))
        self.assertGreater(len(body), 0)

    def test_asset_304_when_unchanged(self) -> None:
        _, headers, _ = self._get("/assets/shell.css")
        tag = headers["ETag"]
        status, headers, body = self._get("/assets/shell.css", {"If-None-Match": tag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("ETag"), tag)

    def test_pages_stay_no_store(self) -> None:
        status, headers, _ = self._get("/chats")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_json_stays_no_store(self) -> None:
        status, headers, _ = self._get(
            "/v1/snapshot",
            {"Authorization": "Bearer secret-token-value"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIsNone(headers.get("ETag"))


if __name__ == "__main__":
    unittest.main()
