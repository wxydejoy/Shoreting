"""控制台替手机管背景：/api/phone/backgrounds 这一层。

手机自己不监听 Hub 的端口，是它自己起了一个小页面（:17920）管背景库。控制台
不复制这套逻辑，只负责三件事，这份测试就盯这三件：

1. 地址从三端地址簿取，**host 连不上要退到 ip**（Mac 的默认路由不在同一个网段
   时 .lan 解不出来，但局域网 IP 一直通）；
2. 口令用 Hub Token 换一个会话 cookie，401 就重登一次（手机重启过会话会丢）；
3. 上传是流式转发的：几十 MB 的视频不能先读进内存再发。

这里起一个假手机（FakePhone）和一个真控制台，两边都打在 127.0.0.1 上。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dock_console import phone_bg
from dock_console.server import serve

COOKIE = "session-abc"


class FakePhone(BaseHTTPRequestHandler):
    """手机端 BackgroundServer 的最小替身。"""

    # 让每个用例自己决定行为
    stale_first: bool = False
    token: str = "hub-token"
    current: str | None = "b.mp4"
    items: list[dict] = []
    uploads: list[tuple[str, int]] = []
    logins: int = 0
    calls: list[tuple[str, str]] = []
    layout: dict | None = None  # None = 旧 APK，没有这个接口
    media: dict[str, bytes] = {}

    def do_POST(self) -> None:  # noqa: N802
        path = self.path
        type(self).calls.append(("POST", path))
        if path == "/api/login":
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length) or b"{}")
            type(self).logins += 1
            if payload.get("token") != self.token:
                self._json(401, {"error": "口令不对"})
                return
            self._json(200, {"ok": True}, cookie=f"dock_bg={COOKIE}")
            return
        if not self._authed():
            return
        if path == "/api/backgrounds":
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b""
            type(self).uploads.append((self.headers.get("Content-Type") or "", len(body)))
            type(self).current = "new.mp4"
            self._json(201, self._index())
            return
        if path == "/api/backgrounds/clear":
            type(self).current = None
            self._json(200, self._index())
            return
        if path.endswith("/select"):
            # 真机上 BackgroundServer 会 decodePart；这里跟着解一次
            ident = urllib.parse.unquote(path.split("/")[3])
            type(self).current = ident
            self._json(200, self._index())
            return
        self._json(404, {"error": "没有这个地址"})

    def do_GET(self) -> None:  # noqa: N802
        type(self).calls.append(("GET", self.path))
        if not self._authed():
            return
        if self.path == "/api/backgrounds":
            self._json(200, self._index())
            return
        if self.path == "/api/layout":
            if type(self).layout is None:
                self._json(404, {"error": "没有这个地址"})
                return
            self._json(200, type(self).layout)
            return
        if self.path.startswith("/api/backgrounds/") and self.path.endswith("/media"):
            ident = urllib.parse.unquote(self.path.split("/")[3])
            blob = type(self).media.get(ident)
            if blob is None:
                self._json(404, {"error": "找不到文件"})
                return
            self._bytes(blob, "image/jpeg")
            return
        self._json(404, {"error": "没有这个地址"})

    def do_DELETE(self) -> None:  # noqa: N802
        type(self).calls.append(("DELETE", self.path))
        if not self._authed():
            return
        ident = urllib.parse.unquote(self.path.split("/")[3])
        type(self).items = [item for item in type(self).items if item["id"] != ident]
        if type(self).current == ident:
            type(self).current = None
        self._json(200, self._index())

    def _authed(self) -> bool:
        if type(self).stale_first:
            # 手机重启过：不管 cookie 是什么，这一次都说会话过期
            type(self).stale_first = False
            self._json(401, {"error": "会话过期"})
            return False
        cookie = self.headers.get("Cookie") or ""
        if cookie == f"dock_bg={COOKIE}":
            return True
        self._json(401, {"error": "口令不对"})
        return False

    def _index(self) -> dict:
        return {"current": type(self).current, "items": type(self).items}

    def _json(self, code: int, payload: dict, cookie: str = "") -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie + "; HttpOnly; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, blob: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def log_message(self, fmt: str, *args) -> None:
        return


def _reset_phone() -> None:
    FakePhone.stale_first = False
    FakePhone.current = "b.mp4"
    FakePhone.items = [
        {"id": "a.mp4", "name": "a.mp4", "kind": "video", "bytes": 1024},
        {"id": "b.mp4", "name": "b.mp4", "kind": "video", "bytes": 2048},
    ]
    FakePhone.uploads = []
    FakePhone.logins = 0
    FakePhone.calls = []
    FakePhone.layout = None
    FakePhone.media = {}


class PhoneBgTest(unittest.TestCase):
    def setUp(self) -> None:
        _reset_phone()
        phone_bg.reset()
        self.phone = ThreadingHTTPServer(("127.0.0.1", 0), FakePhone)
        threading.Thread(target=self.phone.serve_forever, daemon=True).start()
        self.addCleanup(self.phone.server_close)
        self.addCleanup(self.phone.shutdown)
        self.port = self.phone.server_address[1]

        for name, value in (
            ("PHONE_PORT", self.port),
            ("candidates", lambda: ["127.0.0.1"]),
            ("hub_token", lambda: "hub-token"),
        ):
            original = getattr(phone_bg, name)
            setattr(phone_bg, name, value)
            self.addCleanup(setattr, phone_bg, name, original)

    def test_list_returns_items_and_address(self) -> None:
        data = phone_bg.list_backgrounds()
        self.assertTrue(data["ok"])
        self.assertEqual(data["current"], "b.mp4")
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["address"], "127.0.0.1")
        self.assertEqual(FakePhone.logins, 1)

    def test_select_and_delete(self) -> None:
        phone_bg.select("a.mp4")
        self.assertEqual(FakePhone.current, "a.mp4")
        phone_bg.delete("a.mp4")
        self.assertNotIn("a.mp4", [item["id"] for item in FakePhone.items])

    def test_clear_reports_null_current(self) -> None:
        data = phone_bg.clear()
        self.assertIsNone(data["current"])

    def test_expired_session_relogs_once(self) -> None:
        phone_bg.list_backgrounds()  # 先正常登录
        FakePhone.stale_first = True  # 手机重启过，会话没了
        data = phone_bg.list_backgrounds()
        self.assertTrue(data["ok"])
        self.assertEqual(FakePhone.logins, 2)

    def test_bad_token_is_a_readable_error(self) -> None:
        original = phone_bg.hub_token
        phone_bg.hub_token = lambda: "wrong"
        self.addCleanup(setattr, phone_bg, "hub_token", original)
        with self.assertRaises(phone_bg.PhoneError) as ctx:
            phone_bg.list_backgrounds()
        self.assertIn("Token", str(ctx.exception))

    def test_unreachable_phone_says_so(self) -> None:
        phone_bg.candidates = lambda: ["127.0.0.1"]
        phone_bg.PHONE_PORT = 1  # 没人听
        with self.assertRaises(phone_bg.PhoneError) as ctx:
            phone_bg.list_backgrounds()
        self.assertIn("连不上手机", str(ctx.exception))

    def test_upload_streams_in_chunks(self) -> None:
        # 100KB 的「视频」分块喂进去：不能整段先读进内存
        total = 100_000
        payload = b"y" * total
        left = {"value": total}
        sizes: list[int] = []

        def body(size: int) -> bytes:
            sizes.append(size)
            if left["value"] <= 0:
                return b""
            take = min(size, left["value"])
            left["value"] -= take
            return payload[:take]

        data = phone_bg.upload(body, "multipart/form-data; boundary=x", total)
        self.assertTrue(data["ok"])
        self.assertEqual(len(FakePhone.uploads), 1)
        self.assertEqual(FakePhone.uploads[0][1], total)
        # 分块读：每次不超过 CHUNK，且确实读了好几轮
        self.assertGreater(len(sizes), 1)
        self.assertTrue(all(size <= phone_bg.CHUNK for size in sizes))

    def test_upload_too_large_is_refused_before_connecting(self) -> None:
        with self.assertRaises(phone_bg.PhoneError) as ctx:
            phone_bg.upload(lambda size: b"", "multipart/form-data", phone_bg.MAX_UPLOAD + 1)
        self.assertIn("200MB", str(ctx.exception))
        self.assertEqual(FakePhone.calls, [])


class PhoneExtrasTest(PhoneBgTest):
    """布局快照与预览图：手机上的新接口，控制台这边只做搬运与缓存。"""

    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="dock-bg-test-")
        phone_bg.set_cache_dir(pathlib.Path(self.tmp))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_layout_snapshot_roundtrip(self) -> None:
        FakePhone.layout = {"ok": True, "canvas": {"w": 10, "h": 20}, "slots": [], "defaults": {}, "modules": {}}
        data = phone_bg.get_layout()
        self.assertTrue(data["ok"])
        self.assertEqual(data["canvas"], {"w": 10, "h": 20})

    def test_layout_on_old_apk_says_update(self) -> None:
        with self.assertRaises(phone_bg.PhoneError) as ctx:
            phone_bg.get_layout()
        self.assertIn("版本太旧", str(ctx.exception))

    def test_thumbnail_image_passthrough_and_cache(self) -> None:
        FakePhone.media["p.png"] = b"\x89PNG-fake"
        data = phone_bg.thumbnail("p.png", kind="image", size=9)
        self.assertEqual(data["bytes"], b"\x89PNG-fake")
        self.assertEqual(data["content_type"], "image/png")
        # 第二次走缓存：手机那边一个文件都不给也拿得到
        FakePhone.media.clear()
        again = phone_bg.thumbnail("p.png", kind="image", size=9)
        self.assertEqual(again["bytes"], b"\x89PNG-fake")
        self.assertEqual(phone_bg.cached_meta("p.png", 9), {})

    @unittest.skipUnless(phone_bg._tool("ffmpeg"), "需要 ffmpeg")
    def test_thumbnail_video_extracts_frame(self) -> None:
        import subprocess

        clip = pathlib.Path(self.tmp) / "gen.mp4"
        subprocess.run(
            [
                phone_bg._tool("ffmpeg"), "-nostdin", "-v", "error",
                "-f", "lavfi", "-i", "color=c=red:s=64x64:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(clip),
            ],
            check=True,
            capture_output=True,
        )
        blob = clip.read_bytes()
        FakePhone.media["v.mp4"] = blob
        data = phone_bg.thumbnail("v.mp4", kind="video", size=len(blob))
        self.assertEqual(data["content_type"], "image/jpeg")
        self.assertGreater(len(data["bytes"]), 0)
        meta = phone_bg.cached_meta("v.mp4", len(blob))
        self.assertAlmostEqual(meta["duration"], 2.0, delta=0.3)
        self.assertEqual(meta["width"], 64)
        self.assertEqual(meta["height"], 64)


class PhoneRouteTest(unittest.TestCase):
    """控制台自己的路由：把上面这些翻成 /api/phone/*。"""

    def setUp(self) -> None:
        _reset_phone()
        phone_bg.reset()
        self.phone = ThreadingHTTPServer(("127.0.0.1", 0), FakePhone)
        threading.Thread(target=self.phone.serve_forever, daemon=True).start()
        self.addCleanup(self.phone.server_close)
        self.addCleanup(self.phone.shutdown)

        for name, value in (
            ("PHONE_PORT", self.phone.server_address[1]),
            ("candidates", lambda: ["127.0.0.1"]),
            ("hub_token", lambda: "hub-token"),
        ):
            original = getattr(phone_bg, name)
            setattr(phone_bg, name, value)
            self.addCleanup(setattr, phone_bg, name, original)

        class FakeController:
            def snapshot(self) -> list[dict]:
                return []

            def apply(self, service_id: str, action: str) -> dict:
                raise KeyError(service_id)

            def apply_all(self, action: str) -> dict:
                return {"ok": True}

            def set_windows_address(self, address: str) -> dict:
                return {"ok": True}

        self.httpd = serve(FakeController(), host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read() or b"{}")

    def test_list_route(self) -> None:
        status, body = self._get("/api/phone/backgrounds")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["items"]), 2)

    def test_select_route_keeps_chinese_name(self) -> None:
        ident = "守岸人 1080p.mp4"
        FakePhone.items = [{"id": ident, "name": ident, "kind": "video", "bytes": 1}]
        quoted = urllib.parse.quote(ident)
        request = urllib.request.Request(
            f"{self.base}/api/phone/backgrounds/{quoted}/select",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read())
        self.assertEqual(body["current"], ident)

    def test_delete_route(self) -> None:
        request = urllib.request.Request(f"{self.base}/api/phone/backgrounds/a.mp4", method="DELETE")
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read())
        self.assertEqual([item["id"] for item in body["items"]], ["b.mp4"])

    def test_phone_down_returns_502_with_message(self) -> None:
        phone_bg.PHONE_PORT = 1
        status, body = self._get("/api/phone/backgrounds")
        self.assertEqual(status, 502)
        self.assertFalse(body["ok"])
        self.assertIn("连不上手机", body["message"])

    def test_layout_route(self) -> None:
        FakePhone.layout = {"ok": True, "canvas": {"w": 10, "h": 20}, "slots": [], "defaults": {}, "modules": {}}
        status, body = self._get("/api/phone/layout")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_layout_route_old_apk(self) -> None:
        status, body = self._get("/api/phone/layout")
        self.assertEqual(status, 502)
        self.assertIn("版本太旧", body["message"])

    def test_thumb_route_returns_image_bytes(self) -> None:
        import tempfile

        phone_bg.set_cache_dir(pathlib.Path(tempfile.mkdtemp(prefix="dock-bg-route-")))
        FakePhone.media["p.png"] = b"PNG-BYTES"
        with urllib.request.urlopen(
            f"{self.base}/api/phone/backgrounds/p.png/thumb?kind=image&size=9", timeout=5
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.headers.get("Content-Type", "").startswith("image/"))
            self.assertEqual(response.read(), b"PNG-BYTES")

    def test_upload_route_forwards_bytes(self) -> None:
        payload = b"--x\r\ncontent\r\n--x--"
        request = urllib.request.Request(
            f"{self.base}/api/phone/backgrounds",
            data=payload,
            method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=x"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 201)
            body = json.loads(response.read())
        self.assertTrue(body["ok"])
        self.assertEqual(FakePhone.uploads[0][1], len(payload))


if __name__ == "__main__":
    unittest.main()
