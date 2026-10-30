from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from dock_hub.accesslog import AccessLog, redact
from dock_hub.config import parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub


def _lines(directory: Path) -> list[str]:
    today = date.today().isoformat()
    path = directory / f"access-{today}.log"
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class RedactTest(unittest.TestCase):
    def test_query_is_dropped(self) -> None:
        # ticket 就是登录凭据，不能进日志
        self.assertEqual(redact("/chats/claim?ticket=abc123"), "/chats/claim?…")

    def test_search_text_is_dropped(self) -> None:
        self.assertEqual(redact("/v1/companion/chats?q=今晚失眠"), "/v1/companion/chats?…")

    def test_plain_path_untouched(self) -> None:
        self.assertEqual(redact("/v1/snapshot"), "/v1/snapshot")

    def test_bare_question_mark(self) -> None:
        self.assertEqual(redact("/health?"), "/health?…")


class AccessLogFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "logs"

    def test_writes_one_line_per_request(self) -> None:
        log = AccessLog(self.dir, service="hub")
        log.write("10.83.22.40", "GET /v1/snapshot", 200, 1234, 3.4)
        lines = _lines(self.dir)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertIn(" hub ", line)
        self.assertIn(" 10.83.22.40 ", line)
        self.assertIn(" 200 ", line)
        self.assertIn("1234", line)
        self.assertIn("3ms", line)
        self.assertTrue(line.endswith('"GET /v1/snapshot"'))

    def test_query_string_never_reaches_the_file(self) -> None:
        log = AccessLog(self.dir, service="hub")
        log.write("10.0.0.9", "GET /chats/claim?ticket=super-secret", 302, 0, 0.2)
        content = (self.dir / f"access-{date.today().isoformat()}.log").read_text(encoding="utf-8")
        self.assertNotIn("super-secret", content)
        self.assertIn("/chats/claim?…", content)

    def test_disabled_writes_nothing(self) -> None:
        AccessLog(None).write("10.0.0.9", "GET /health", 200, 10, 0.1)
        self.assertEqual(self.dir.parent.exists() and list(self.dir.parent.glob("logs/*")), [])

    def test_only_recent_days_survive(self) -> None:
        self.dir.mkdir(parents=True)
        today = date.today()
        old = self.dir / f"access-{(today - timedelta(days=30)).isoformat()}.log"
        fresh = self.dir / f"access-{(today - timedelta(days=2)).isoformat()}.log"
        stranger = self.dir / "notes.log"
        for path in (old, fresh, stranger):
            path.write_text("x\n", encoding="utf-8")

        log = AccessLog(self.dir, keep_days=7)
        log.write("10.0.0.9", "GET /health", 200, 10, 0.1)

        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        # 不是我们起的名字，别动手
        self.assertTrue(stranger.exists())

    def test_broken_directory_does_not_raise(self) -> None:
        blocker = Path(self.tmp.name) / "not-a-dir"
        blocker.write_text("i am a file", encoding="utf-8")
        log = AccessLog(blocker / "logs")
        log.write("10.0.0.9", "GET /health", 200, 10, 0.1)  # 不该抛

    def test_missing_config_path_means_no_file(self) -> None:
        cfg = parse_config({"name": "study", "token": "secret-token-value"})
        self.assertIsNone(cfg.path)
        self.assertIsNone(AccessLog.from_config(cfg).directory)

    def test_env_zero_turns_it_off(self) -> None:
        cfg = parse_config(
            {"name": "study", "token": "secret-token-value"},
            Path(self.tmp.name) / "hub.yaml",
        )
        with patch.dict(os.environ, {"DOCK_HUB_ACCESS_LOG": "0"}):
            self.assertIsNone(AccessLog.from_config(cfg).directory)

    def test_keep_days_env(self) -> None:
        cfg = parse_config(
            {"name": "study", "token": "secret-token-value"},
            Path(self.tmp.name) / "hub.yaml",
        )
        with patch.dict(os.environ, {"DOCK_HUB_LOG_KEEP_DAYS": "3"}):
            log = AccessLog.from_config(cfg)
        self.assertEqual(log.keep_days, 3)


class AccessLogHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "mini": False,
            },
            root / "hub.yaml",
        )
        self.hub = DockHub(cfg)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        self.dir = root / "logs"

    def _get(self, path: str) -> int:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers={"Authorization": "Bearer secret-token-value"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status

    def test_request_lands_in_the_file(self) -> None:
        self.assertEqual(self._get("/health"), 200)
        lines = _lines(self.dir)
        self.assertEqual(len(lines), 1)
        self.assertIn("GET /health", lines[0])
        self.assertIn(" 200 ", lines[0])

    def test_snapshot_query_and_token_stay_out(self) -> None:
        query = quote("失眠")
        self.assertEqual(self._get(f"/v1/companion/chats?q={query}&limit=5"), 200)
        content = (self.dir / f"access-{date.today().isoformat()}.log").read_text(encoding="utf-8")
        self.assertIn("?…", content)
        # 搜过的词既不该是原文，也不该是百分号编码
        self.assertNotIn(query, content)
        self.assertNotIn("失眠", content)
        self.assertNotIn("secret-token-value", content)

    def test_env_off_leaves_no_file(self) -> None:
        with patch.dict(os.environ, {"DOCK_HUB_ACCESS_LOG": "0"}):
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/health")
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
        self.assertEqual(_lines(self.dir), [])


if __name__ == "__main__":
    unittest.main()
