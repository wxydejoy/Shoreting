from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config, save_config_raw
from dock_hub.server import make_handler
from dock_hub.service import DockHub
from dock_hub.setup_ui import is_local_client


class SetupWizardTest(unittest.TestCase):
    def test_is_local_client(self) -> None:
        self.assertTrue(is_local_client(("127.0.0.1", 1234)))
        self.assertTrue(is_local_client(("::1", 1234)))
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

        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/setup/api/state")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body["config"]["name"], "study")
        conn.close()


if __name__ == "__main__":
    unittest.main()
