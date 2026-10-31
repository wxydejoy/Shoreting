from __future__ import annotations

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dock_console.features import FeaturesClient, default_transport
from dock_console.server import serve


ROOT = Path(__file__).resolve().parents[2]


class FakeFeatures:
    def __init__(self) -> None:
        self.saved: list[tuple[str, object]] = []

    def catalog(self):
        return {
            "ok": True,
            "groups": [{"id": "talk", "title": "主动开口", "blurb": "总闸"}],
            "features": [
                {
                    "key": "proactive.enabled",
                    "group": "talk",
                    "label": "主动开口",
                    "hint": "总闸",
                    "kind": "bool",
                    "value": True,
                }
            ],
            "config_path": "/tmp/hub.yaml",
        }

    def save(self, key, value):
        self.saved.append((key, value))
        return {"ok": True, "key": key, "value": not value, "message": "已生效，不用重启"}


class FakeController:
    def snapshot(self):
        return []

    def apply(self, service_id: str, action: str):
        return {"ok": True, "id": service_id, "action": action, "message": "已启动"}


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.features = FakeFeatures()
        self.httpd = serve(FakeController(), host="127.0.0.1", port=0, features=self.features)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def test_features_page_is_served(self) -> None:
        # 单页应用：/features 和 / 发的是同一份壳，界面由 console.js 按 # 路由
        page = urllib.request.urlopen(self.base + "/features", timeout=3).read().decode()
        self.assertIn("岸亭控制台", page)
        self.assertIn("/assets/console.js", page)

    def test_index_and_features_share_one_shell(self) -> None:
        index = urllib.request.urlopen(self.base + "/", timeout=3).read().decode()
        features = urllib.request.urlopen(self.base + "/features", timeout=3).read().decode()
        self.assertEqual(index, features)

    def test_catalog_is_proxied(self) -> None:
        body = json.loads(urllib.request.urlopen(self.base + "/api/features", timeout=3).read())
        self.assertTrue(body["ok"])
        self.assertEqual(body["features"][0]["key"], "proactive.enabled")

    def test_save_is_proxied(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/features",
            method="POST",
            data=json.dumps({"key": "proactive.enabled", "value": False}).encode(),
            headers={"Content-Type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(request, timeout=3).read())
        self.assertTrue(body["ok"])
        self.assertEqual(self.features.saved, [("proactive.enabled", False)])

    def test_save_without_key_is_rejected(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/features",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.assertEqual(self.features.saved, [])


class ClientTest(unittest.TestCase):
    def test_4xx_body_is_closed(self) -> None:
        # Hub 拒绝改值时回 4xx，这条响应也得关掉，不然连接一直挂着
        error = urllib.error.HTTPError(
            "http://127.0.0.1/x", 400, "Bad Request", {}, io.BytesIO(b'{"message": "\\u4e0d\\u884c"}')
        )
        closed: list[bool] = []
        error.close = lambda: closed.append(True)  # type: ignore[method-assign]

        def opener(request, timeout=None):
            raise error

        result = default_transport("POST", "/setup/api/features", {}, opener=opener)
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "不行")
        self.assertEqual(closed, [True])

    def test_client_hits_the_setup_api(self) -> None:
        calls: list[tuple[str, str, object]] = []

        def transport(method, path, body):
            calls.append((method, path, body))
            return {"ok": True}

        client = FeaturesClient(transport=transport)
        client.catalog()
        client.save("guest.enabled", False)
        self.assertEqual(calls[0], ("GET", "/setup/api/features", None))
        self.assertEqual(calls[1], ("POST", "/setup/api/features", {"key": "guest.enabled", "value": False}))


class TransportTest(unittest.TestCase):
    def test_hub_down_is_a_sentence_not_a_traceback(self) -> None:
        def down(*args, **kwargs):
            raise urllib.error.URLError("Connection refused")

        result = default_transport(
            "GET", "/setup/api/features", None, base_url="http://127.0.0.1:9", opener=down
        )
        self.assertFalse(result["ok"])
        self.assertIn("Hub 没接上", result["message"])

    def test_hub_rejection_keeps_its_message(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.dumps({"ok": False, "message": "每天最多说几句 要在 0–12 之间"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        result = default_transport("POST", "/setup/api/features", {"key": "x", "value": 1}, base_url=base)
        self.assertFalse(result["ok"])
        self.assertIn("0–12", result["message"])


if __name__ == "__main__":
    unittest.main()
