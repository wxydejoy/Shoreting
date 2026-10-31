"""控制台的 /api/hub/* 转发。

前端不再跨端口打 Hub，这一层负责把路径、查询串、请求体和响应头原样搬过去。
这里起一个假 Hub 来验：路径没被改写、POST 的 Content-Type 还在、二进制不被
当成文本、连不上时回的是 502 而不是 500。
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dock_console import hub_proxy
from dock_console.server import serve

WAV = b"RIFF$\x00\x00\x00WAVEfmt "


class FakeHub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/audio/"):
            self._send(200, "audio/wav", WAV)
            return
        self._send(200, "application/json; charset=utf-8", json.dumps({"path": self.path}).encode())

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        payload = {
            "path": self.path,
            "content_type": self.headers.get("Content-Type") or "",
            "filename": self.headers.get("X-Filename") or "",
            "body": raw.decode("utf-8", errors="replace"),
        }
        self._send(200, "application/json", json.dumps(payload).encode())

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(200, "application/json", json.dumps({"deleted": self.path}).encode())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


class FakeController:
    def snapshot(self) -> list[dict]:
        return []

    def apply(self, service_id: str, action: str) -> dict:
        raise KeyError(service_id)

    def apply_all(self, action: str) -> dict:
        return {"ok": True, "action": action, "message": "好"}

    def set_windows_address(self, address: str) -> dict:
        return {"ok": True}


class HubProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
        threading.Thread(target=self.hub.serve_forever, daemon=True).start()
        self.addCleanup(self.hub.server_close)
        self.addCleanup(self.hub.shutdown)

        original = hub_proxy.HUB_BASE
        hub_proxy.HUB_BASE = f"http://127.0.0.1:{self.hub.server_address[1]}"
        self.addCleanup(setattr, hub_proxy, "HUB_BASE", original)

        self.httpd = serve(FakeController(), host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def test_get_keeps_path_and_query(self) -> None:
        body = json.loads(
            urllib.request.urlopen(
                self.base + "/api/hub/v1/companion/chats?limit=2&q=%E7%81%AF", timeout=3
            ).read()
        )
        self.assertEqual(body["path"], "/v1/companion/chats?limit=2&q=%E7%81%AF")

    def test_binary_and_content_type_survive(self) -> None:
        response = urllib.request.urlopen(self.base + "/api/hub/audio/1", timeout=3)
        self.assertEqual(response.headers["Content-Type"], "audio/wav")
        self.assertEqual(response.read(), WAV)

    def test_post_carries_body_without_origin(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/hub/setup/api/save",
            data=json.dumps({"name": "study"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(request, timeout=3).read())
        self.assertEqual(body["path"], "/setup/api/save")
        self.assertEqual(body["content_type"], "application/json")
        self.assertEqual(json.loads(body["body"]), {"name": "study"})

    def test_delete_passes_through(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/hub/v1/companion/diary/2026-10-31", method="DELETE"
        )
        body = json.loads(urllib.request.urlopen(request, timeout=3).read())
        self.assertEqual(body["deleted"], "/v1/companion/diary/2026-10-31")

    def test_upload_keeps_filename_header(self) -> None:
        # 整本书上传：Hub 从 X-Filename 拿书名，控制台只放这一个头过去
        request = urllib.request.Request(
            self.base + "/api/hub/v1/companion/books",
            data="第一章".encode(),
            method="POST",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Filename": "%E6%B5%8B%E8%AF%95.txt",
            },
        )
        body = json.loads(urllib.request.urlopen(request, timeout=3).read())
        self.assertEqual(body["path"], "/v1/companion/books")
        self.assertEqual(body["filename"], "%E6%B5%8B%E8%AF%95.txt")
        self.assertEqual(body["content_type"], "application/octet-stream")
        self.assertEqual(body["body"], "第一章")

    def test_unreachable_hub_is_502(self) -> None:
        hub_proxy.HUB_BASE = "http://127.0.0.1:9"  # 没人监听
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + "/api/hub/health", timeout=5)
        self.assertEqual(caught.exception.code, 502)
        payload = json.loads(caught.exception.read())
        self.assertIn("Hub 没接上", payload["message"])

    def test_parent_segments_are_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + "/api/hub/../etc/passwd", timeout=3)
        self.assertIn(caught.exception.code, {400, 404})


if __name__ == "__main__":
    unittest.main()
