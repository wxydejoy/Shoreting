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
from dock_hub.errors import HubError
from dock_hub.server import make_handler
from dock_hub.service import DockHub
from dock_hub.setup_ui import ensure_local


def _cfg(**extra):
    raw = {
        "name": "study",
        "token": "secret-token-value",
        "host": "127.0.0.1",
        "port": 0,
        "pc": False,
        "media": False,
        "mini": False,
        "devices": [
            {
                "id": "cursor",
                "name": "Cursor",
                "type": "action",
                "path": r"D:\Apps\Cursor.exe",
                "process": ["cursor.exe"],
            }
        ],
    }
    raw.update(extra)
    return parse_config(raw)


class ProbeConfigTest(unittest.TestCase):
    def test_parse_probe_url(self) -> None:
        cfg = _cfg(probe={"url": "http://10.83.22.31:17892", "stale_ms": 2500})
        self.assertEqual(cfg.probe.url, "http://10.83.22.31:17892")
        self.assertEqual(cfg.probe.stale_ms, 2500)
        self.assertEqual(cfg.probe.hub_url, "http://10.83.22.121:17890")
        self.assertEqual(cfg.probe.listen, "0.0.0.0:17892")


class HubLanPagesTest(unittest.TestCase):
    def test_pages_default_mini_hub(self) -> None:
        from dock_hub.probe import hub_lan_pages

        pages = hub_lan_pages(_cfg())
        self.assertEqual(pages["setup"], "http://10.83.22.121:17890/setup")
        self.assertEqual(pages["setup_login"], "http://10.83.22.121:17890/chats?next=/setup")

    def test_pages_follow_hub_url(self) -> None:
        from dock_hub.probe import hub_lan_pages

        cfg = _cfg(probe={"hub_url": "http://10.83.22.121:17890"})
        pages = hub_lan_pages(cfg)
        self.assertEqual(pages["setup"], "http://10.83.22.121:17890/setup")
        self.assertEqual(pages["setup_login"], "http://10.83.22.121:17890/chats?next=/setup")
        self.assertEqual(pages["chats"], "http://10.83.22.121:17890/chats")
        self.assertTrue(pages["setup"].endswith(":17890/setup"))


class AutoLoginTicketTest(unittest.TestCase):
    def test_ticket_claim_sets_cookie_and_redirects(self) -> None:
        hub = DockHub(_cfg())
        self.addCleanup(hub.pc.stop)
        self.addCleanup(hub.reporter.stop)
        minted = hub.mint_login_ticket("/setup")
        self.assertTrue(minted["ok"])
        self.assertIn("/chats/claim?ticket=", minted["path"])
        next_path = hub.claim_login_ticket(minted["ticket"])
        self.assertEqual(next_path, "/setup")
        self.assertIsNone(hub.claim_login_ticket(minted["ticket"]))

    def test_mint_claim_url_ignores_proxy_env(self) -> None:
        # 环境里挂着 http_proxy 时（桌面端常见），urlopen 会把对 Hub 的
        # 回环请求也送进代理—— intermittent 502，自动登录跟着挂。
        # mint_claim_url 必须直连局域网 / 回环，不读代理环境变量。
        import os
        from dock_hub.probe import mint_claim_url

        hub = DockHub(_cfg())
        self.addCleanup(hub.pc.stop)
        self.addCleanup(hub.reporter.stop)
        hub_handler = make_handler(hub)
        hub_server = ThreadingHTTPServer(("127.0.0.1", 0), hub_handler)
        threading.Thread(target=hub_server.serve_forever, daemon=True).start()
        self.addCleanup(hub_server.server_close)
        self.addCleanup(hub_server.shutdown)
        cfg = _cfg(probe={"hub_url": f"http://127.0.0.1:{hub_server.server_address[1]}"})
        with patch.dict(
            os.environ,
            {
                "http_proxy": "http://127.0.0.1:9",
                "https_proxy": "http://127.0.0.1:9",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
            },
        ):
            url = mint_claim_url(cfg, "/setup")  # 代理端口 9 是死的，直连才能活
        self.assertIn("/chats/claim?ticket=", url)

    def test_probe_open_setup_redirects_to_claim(self) -> None:
        from dock_hub.probe import make_probe_handler

        hub = DockHub(_cfg(host="127.0.0.1", mini=False))
        self.addCleanup(hub.pc.stop)
        self.addCleanup(hub.reporter.stop)
        hub_handler = make_handler(hub)
        hub_server = ThreadingHTTPServer(("127.0.0.1", 0), hub_handler)
        hub_thread = threading.Thread(target=hub_server.serve_forever, daemon=True)
        hub_thread.start()
        self.addCleanup(hub_server.server_close)
        self.addCleanup(hub_server.shutdown)
        hub_port = hub_server.server_address[1]

        probe_cfg = _cfg(probe={"hub_url": f"http://127.0.0.1:{hub_port}", "listen": "127.0.0.1:0"})
        probe_server = ThreadingHTTPServer(("127.0.0.1", 0), make_probe_handler(probe_cfg))
        probe_thread = threading.Thread(target=probe_server.serve_forever, daemon=True)
        probe_thread.start()
        self.addCleanup(probe_server.server_close)
        self.addCleanup(probe_server.shutdown)
        probe_port = probe_server.server_address[1]

        conn = HTTPConnection("127.0.0.1", probe_port, timeout=5)
        conn.request("GET", "/v1/open-setup")
        resp = conn.getresponse()
        location = resp.getheader("Location") or ""
        conn.close()
        self.assertEqual(resp.status, 302)
        self.assertIn(f"http://127.0.0.1:{hub_port}/chats/claim?ticket=", location)
        self.assertIn("next=/setup", location)

        conn = HTTPConnection("127.0.0.1", hub_port, timeout=5)
        path = location.split(f"http://127.0.0.1:{hub_port}", 1)[1]
        conn.request("GET", path)
        resp = conn.getresponse()
        cookie = resp.getheader("Set-Cookie") or ""
        conn.close()
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "/setup")
        self.assertIn("helm_chats=", cookie)
        self.assertNotIn("secret-token-value", location)


class ProbeTelemetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = DockHub(
            _cfg(probe={"url": "http://10.83.22.31:17892", "stale_ms": 200})
        )
        self.addCleanup(self.hub.pc.stop)
        self.addCleanup(self.hub.reporter.stop)

    def test_fresh_telemetry_fills_snapshot_pc(self) -> None:
        self.hub.ingest_telemetry(
            {
                "pc": {
                    "online": True,
                    "cpu": {"percent": 12.5},
                    "memory": {"percent": 40.0},
                },
                "running": ["cursor.exe", "explorer.exe"],
            }
        )
        snap = self.hub.snapshot()
        self.assertTrue(snap["pc"]["online"])
        self.assertEqual(snap["pc"]["cpu"]["percent"], 12.5)
        cursor = next(item for item in snap["devices"] if item["id"] == "cursor")
        self.assertTrue(cursor["online"])
        self.assertTrue(cursor["on"])

    def test_stale_telemetry_marks_pc_offline(self) -> None:
        clock = {"t": 1000.0}
        with patch("dock_hub.service.time.monotonic", side_effect=lambda: clock["t"]):
            self.hub.ingest_telemetry({"pc": {"online": True, "cpu": {"percent": 1}}})
            clock["t"] += 0.5
            snap = self.hub.snapshot()
        self.assertFalse(snap["pc"]["online"])
        cursor = next(item for item in snap["devices"] if item["id"] == "cursor")
        self.assertFalse(cursor["online"])
        self.assertFalse(cursor["on"])

    def test_no_telemetry_yet_is_offline(self) -> None:
        snap = self.hub.snapshot()
        self.assertFalse(snap["pc"]["online"])

    def test_action_forwards_to_probe_not_local_launch(self) -> None:
        self.hub.ingest_telemetry({"pc": {"online": True}})
        with patch("dock_hub.service.launch") as local:
            with patch.object(self.hub, "_probe_launch") as remote:
                body = self.hub.command("cursor", {"run": True})
        local.assert_not_called()
        remote.assert_called_once()
        self.assertEqual(body["id"], "cursor")
        self.assertIn("last_run_at", body)

    def test_action_offline_when_pc_off(self) -> None:
        with self.assertRaises(HubError) as ctx:
            self.hub.command("cursor", {"run": True})
        self.assertEqual(ctx.exception.code, "offline")


class ProbeProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = DockHub(_cfg(probe={"url": "http://10.83.22.31:17892"}))
        self.addCleanup(self.hub.pc.stop)
        self.addCleanup(self.hub.reporter.stop)
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

    def test_telemetry_endpoint(self) -> None:
        status, body = self._request(
            "POST",
            "/v1/telemetry",
            {"pc": {"online": True, "cpu": {"percent": 9}}, "running": ["cursor.exe"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        snap_status, snap = self._request("GET", "/v1/snapshot")
        self.assertEqual(snap_status, 200)
        self.assertTrue(snap["pc"]["online"])
        self.assertEqual(snap["pc"]["cpu"]["percent"], 9)

    def test_telemetry_unauthorized(self) -> None:
        status, body = self._request("POST", "/v1/telemetry", {"pc": {"online": True}}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")


class SetupLanTest(unittest.TestCase):
    def test_setup_allows_bearer_from_lan(self) -> None:
        class Handler:
            client_address = ("10.83.22.31", 9)
            headers = {"Authorization": "Bearer secret-token-value"}

            def _chats_ok(self) -> bool:
                return True

        ensure_local(Handler())

    def test_setup_rejects_lan_without_token(self) -> None:
        class Handler:
            client_address = ("10.83.22.31", 9)
            headers = {}

            def _chats_ok(self) -> bool:
                return False

        with self.assertRaises(HubError) as ctx:
            ensure_local(Handler())
        self.assertEqual(ctx.exception.code, "forbidden")


class ProbeServerTest(unittest.TestCase):
    def test_health_and_launch(self) -> None:
        from dock_hub.probe import make_probe_handler, parse_listen

        host, port = parse_listen("127.0.0.1:0")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 0)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        marker = Path(tmp.name) / "cursor.exe"
        marker.write_text("fake", encoding="utf-8")
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "probe": {"hub_url": "http://127.0.0.1:9", "listen": "127.0.0.1:0"},
                "devices": [
                    {
                        "id": "cursor",
                        "name": "Cursor",
                        "type": "action",
                        "path": str(marker),
                    }
                ],
                "mini": False,
            }
        )
        launched = []

        def capture(device):
            launched.append(device.id)

        with patch("dock_hub.probe.launch", side_effect=capture):
            handler = make_probe_handler(cfg)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            health = json.loads(resp.read().decode("utf-8"))
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertEqual(health["service"], "dock-probe")
            self.assertEqual(health["name"], "windows")

            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            raw = json.dumps({"id": "cursor", "run": True}).encode("utf-8")
            conn.request(
                "POST",
                "/v1/launch",
                body=raw,
                headers={
                    "Authorization": "Bearer secret-token-value",
                    "Content-Type": "application/json",
                },
            )
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(launched, ["cursor"])


if __name__ == "__main__":
    unittest.main()
