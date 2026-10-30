from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config, save_config_raw
from dock_hub.errors import HubError
from dock_hub.server import make_handler
from dock_hub.service import DockHub
from dock_hub.setup_ui import guard_write, is_local_client


class CsrfGuardTest(unittest.TestCase):
    def _handler(self, content_type: str = "application/json", origin: str | None = None):
        class Fake:
            headers = {
                "Content-Type": content_type,
                "Host": "127.0.0.1:17890",
            }

        if origin is not None:
            Fake.headers["Origin"] = origin
        return Fake()

    def test_rejects_non_json_content_type(self) -> None:
        # 跨站简单请求只能用 text/plain，必须挡在解析之前
        with self.assertRaises(HubError) as ctx:
            guard_write(self._handler(content_type="text/plain"))
        self.assertEqual(ctx.exception.status, 415)

    def test_rejects_cross_origin(self) -> None:
        with self.assertRaises(HubError) as ctx:
            guard_write(self._handler(origin="http://evil.example"))
        self.assertEqual(ctx.exception.status, 403)

    def test_allows_same_origin_and_curl(self) -> None:
        guard_write(self._handler(origin="http://127.0.0.1:17890"))
        guard_write(self._handler())


class SetupWizardTest(unittest.TestCase):
    def test_is_local_client(self) -> None:
        self.assertTrue(is_local_client(("127.0.0.1", 1234)))
        self.assertTrue(is_local_client(("::1", 1234)))
        self.assertTrue(is_local_client(("::ffff:127.0.0.1", 1234)))
        self.assertFalse(is_local_client(("10.0.0.1", 1234)))

    def test_save_config_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            raw = {
                "name": "study",
                "token": "secret-token-value",
                "host": "0.0.0.0",
                "port": 17890,
                "devices": [
                    {
                        "id": "lamp",
                        "name": "台灯",
                        "type": "light",
                        "mijia_name": "台灯",
                        "on_prop": "on-2",
                    }
                ],
            }
            cfg = save_config_raw(raw, path)
            self.assertEqual(cfg.devices[0].mijia_name, "台灯")
            dumped = hub_config_to_raw(cfg)
            self.assertEqual(dumped["devices"][0]["on_prop"], "on-2")
            self.assertTrue(path.is_file())

    def test_wizard_save_keeps_companion(self) -> None:
        from dock_hub.setup_ui import _payload_to_raw

        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "companion": {
                    "enabled": True,
                    "llm": {"base_url": "http://127.0.0.1:11434", "model": "qwen3.5:4b"},
                    "tts": {"base_url": "http://127.0.0.1:18100"},
                },
                "devices": [],
            }
        )
        raw = _payload_to_raw(
            {"name": "study", "token": "secret-token-value", "port": 17890, "devices": []},
            cfg,
        )
        self.assertEqual(raw["companion"]["llm"]["model"], "qwen3.5:4b")
        self.assertEqual(raw["companion"]["tts"]["base_url"], "http://127.0.0.1:18100")
        self.assertEqual(raw["mini"]["base_url"], "http://10.83.22.121:17891")

    def test_wizard_save_keeps_proactive(self) -> None:
        from dock_hub.setup_ui import _payload_to_raw

        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "proactive": {
                    "daily_limit": 3,
                    "morning": {"at": "07:30", "text": "早。"},
                },
                "devices": [],
            }
        )
        raw = _payload_to_raw(
            {"name": "study", "token": "secret-token-value", "port": 17890, "devices": []},
            cfg,
        )
        self.assertEqual(raw["proactive"]["daily_limit"], 3)
        self.assertEqual(raw["proactive"]["morning"]["at"], "07:30")

    def test_voice_save_keeps_the_rest_and_clamps(self) -> None:
        from unittest.mock import patch

        from dock_hub.setup_ui import apply_voice

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            raw = {
                "name": "study",
                "token": "secret-token-value",
                "devices": [],
                "companion": {
                    "enabled": True,
                    "llm": {"base_url": "http://127.0.0.1:11434", "model": "qwen3.5:4b"},
                    "tts": {"base_url": "http://127.0.0.1:9"},
                },
            }
            cfg = save_config_raw(raw, path)
            hub = DockHub(cfg)
            self.addCleanup(hub.pc.stop)
            self.addCleanup(hub.reporter.stop)
            with patch("dock_hub.system_volume.write_output_volume") as setter:
                result = apply_voice(hub, {"volume": 35, "speed": 1.2})
            setter.assert_called_once_with(35)
            self.assertTrue(result["ok"])
            self.assertEqual(hub.config.companion.voice_volume, 35)
            self.assertEqual(hub.config.companion.voice_speed, 1.2)
            kept = path.read_text(encoding="utf-8")
            self.assertIn("qwen3.5:4b", kept)
            self.assertIn("volume: 35", kept)
            with self.assertRaises(ValueError):
                apply_voice(hub, {"volume": 140, "speed": 1})

    def test_setup_api_localhost_only(self) -> None:
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
        hub = DockHub(cfg)
        self.addCleanup(hub.pc.stop)
        self.addCleanup(hub.reporter.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)

        # timeout=3 在整包测试的高负载下会偶发超时（DockHub 构造要起
        # reporter / proactive / scheduler 好几条线程），放宽到 10s。
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/setup/api/state")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body["config"]["name"], "study")
        conn.close()


if __name__ == "__main__":
    unittest.main()
