from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection

from dock_hub.config import parse_config
from dock_hub.server import IDLE_TIMEOUT, GuardedHTTPServer, make_handler
from dock_hub.service import DockHub


def _mini_hub() -> DockHub:
    return DockHub(
        parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
                "mini": False,
            }
        )
    )


class _OneSlot(GuardedHTTPServer):
    max_connections = 1
    busy_timeout = 0.2


class GuardedServerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = _mini_hub()
        self.server = _OneSlot(("127.0.0.1", 0), make_handler(self.hub))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _health(self, conn: HTTPConnection) -> int:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        resp.read()
        return resp.status

    def test_handler_has_idle_timeout(self) -> None:
        handler = make_handler(self.hub)
        self.assertEqual(handler.timeout, IDLE_TIMEOUT)
        self.assertGreater(IDLE_TIMEOUT, 0)

    def test_extra_connection_gets_busy(self) -> None:
        first = HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(first.close)
        self.assertEqual(self._health(first), 200)

        second = HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(second.close)
        second.request("GET", "/health")
        resp = second.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 503)
        self.assertEqual(body["error"]["code"], "busy")

    def test_slot_is_released_after_close(self) -> None:
        first = HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.assertEqual(self._health(first), 200)
        first.close()

        freed = False
        for _ in range(60):
            if self.server._slots.acquire(timeout=0.05):  # noqa: SLF001
                self.server._slots.release()  # noqa: SLF001
                freed = True
                break
        self.assertTrue(freed, "连接关掉后名额没还回来")


class _BrokenPipe(io.RawIOBase):
    def write(self, data: bytes) -> int:  # noqa: ARG002
        raise BrokenPipeError("client went away")

    def flush(self) -> None:
        raise BrokenPipeError("client went away")

    def close(self) -> None:
        pass


class DisconnectTest(unittest.TestCase):
    def _handler(self, raw: bytes):
        cls = make_handler(_mini_hub())
        handler = cls.__new__(cls)
        handler.rfile = io.BytesIO(raw)
        handler.wfile = _BrokenPipe()
        handler.close_connection = False
        handler.request_version = "HTTP/1.1"
        handler.client_address = ("127.0.0.1", 5555)
        handler.server = None
        return handler

    def test_health_disconnect_does_not_raise(self) -> None:
        handler = self._handler(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        handler.handle()  # 不该抛，也不该刷 traceback


if __name__ == "__main__":
    unittest.main()
