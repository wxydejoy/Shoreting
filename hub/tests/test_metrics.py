"""metrics.py 与 /metrics 端点。

指标出错的两种方式都很隐蔽：名字写错（grafana 里查不出来）、label 基数爆掉
（把内存吃光）。所以这里除了验数字对不对，也验渲染出来的文本长得像不像
Prometheus 能读的东西。
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from dock_hub.config import parse_config
from dock_hub.metrics import LATENCY_BUCKETS_MS, METRICS, Registry
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = Registry()

    def test_counter_accumulates_and_keeps_labels_separate(self) -> None:
        self.reg.inc("x_total")
        self.reg.inc("x_total")
        self.reg.inc("x_total", reason="a")
        self.assertEqual(self.reg.counter("x_total"), 2)
        self.assertEqual(self.reg.counter("x_total", reason="a"), 1)

    def test_histogram_counts_every_bucket_up_to_the_value(self) -> None:
        self.reg.observe("lat_ms", 700)
        got = self.reg.histogram("lat_ms")
        self.assertEqual(got["count"], 1)
        self.assertEqual(got["sum"], 700)
        # 700 落在 1000 这一档，所以 <=300 那些不该有
        self.assertEqual(got["le_300"], 0)
        self.assertEqual(got["le_1000"], 1)

    def test_first_sentence_reports_ms_once(self) -> None:
        self.reg.note_turn("t1")
        ms = self.reg.first_sentence("t1")
        self.assertIsNotNone(ms)
        assert ms is not None
        self.assertGreaterEqual(ms, 0)
        # 消费过的轮次不再给值，避免重复计时
        self.assertIsNone(self.reg.first_sentence("t1"))

    def test_unknown_turn_returns_none(self) -> None:
        self.assertIsNone(self.reg.first_sentence("nope"))

    def test_turn_table_is_capped(self) -> None:
        for i in range(200):
            self.reg.note_turn(f"t{i}")
        # 上限是 64，最老的应该已经被挤掉
        self.assertIsNone(self.reg.first_sentence("t0"))
        self.assertIsNotNone(self.reg.first_sentence("t199"))

    def test_render_shape(self) -> None:
        self.reg.inc("hub_companion_chat_total")
        self.reg.observe("hub_companion_first_sentence_ms", 412)
        text = self.reg.render({"hub_mijia_login_ok": 1.0})
        self.assertIn("# TYPE hub_companion_chat_total counter", text)
        self.assertIn("hub_companion_chat_total 1", text)
        self.assertIn("# TYPE hub_companion_first_sentence_ms histogram", text)
        self.assertIn('hub_companion_first_sentence_ms_bucket{le="300"} 0', text)
        self.assertIn('hub_companion_first_sentence_ms_bucket{le="+Inf"} 1', text)
        self.assertIn("hub_companion_first_sentence_ms_sum 412", text)
        self.assertIn("hub_companion_first_sentence_ms_count 1", text)
        self.assertIn("hub_mijia_login_ok 1", text)
        self.assertTrue(text.endswith("\n"))

    def test_render_escapes_labels(self) -> None:
        self.reg.inc("x_total", reason='a"b\\c')
        text = self.reg.render()
        self.assertIn('reason="a\\"b\\\\c"', text)

    def test_threads_do_not_lose_counters(self) -> None:
        def bump() -> None:
            for _ in range(200):
                self.reg.inc("y_total")

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.reg.counter("y_total"), 800)

    def test_buckets_are_sorted(self) -> None:
        self.assertEqual(list(LATENCY_BUCKETS_MS), sorted(LATENCY_BUCKETS_MS))


class MetricsEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 计数是进程级的，别的用例也会往里写；每个用例先清零，断言才说得准
        METRICS.reset()
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
            }
        )
        self.hub = DockHub(cfg)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8"), resp.getheader("Content-Type") or ""
        finally:
            conn.close()

    def test_local_client_needs_no_token(self) -> None:
        status, body, ctype = self.get("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", ctype)
        self.assertIn("hub_mijia_login_ok", body)
        self.assertIn("hub_companion_enabled", body)

    def test_snapshot_is_timed(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/v1/snapshot", headers={"Authorization": "Bearer secret-token-value"})
            self.assertEqual(conn.getresponse().status, 200)
        finally:
            conn.close()
        _, body, _ = self.get("/metrics")
        self.assertIn("hub_snapshot_ms_count 1", body)

    def test_remote_client_is_challenged(self) -> None:
        # 本机以外的人不该免费看到她几点在跟谁说话
        status, body, _ = self.get("/metrics", headers={"X-Forwarded-For": "10.0.0.9"})
        self.assertEqual(status, 200)  # 本机判断看 socket，不看头
        self.assertIn("hub_mijia_login_ok", body)


if __name__ == "__main__":
    unittest.main()
