from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from dock_console.server import serve
from dock_console.services import Controller
from dock_console.windows import normalize_windows_address, read_probe_url, write_probe_url


class NormalizeTest(unittest.TestCase):
    def test_bare_ip_uses_probe_port(self) -> None:
        self.assertEqual(normalize_windows_address("10.1.2.3"), "http://10.1.2.3:17892")

    def test_keeps_explicit_port(self) -> None:
        self.assertEqual(
            normalize_windows_address("http://10.1.2.3:17900/"),
            "http://10.1.2.3:17900",
        )

    def test_blank_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_windows_address("  ")


class YamlEditTest(unittest.TestCase):
    def test_replaces_probe_url_and_leaves_secrets(self) -> None:
        original = (
            "token: secret-token\n"
            "companion:\n"
            "  llm:\n"
            "    api_key: sk-test\n"
            "probe:\n"
            "  url: http://10.0.0.8:17892\n"
        )
        updated = write_probe_url(original, "http://10.1.2.3:17892")
        self.assertIn("url: http://10.1.2.3:17892\n", updated)
        self.assertIn("token: secret-token\n", updated)
        self.assertIn("api_key: sk-test\n", updated)
        self.assertNotIn("10.0.0.8", updated)
        self.assertEqual(read_probe_url(updated), "http://10.1.2.3:17892")

    def test_inserts_probe_block_when_missing(self) -> None:
        updated = write_probe_url("name: study\n", "http://10.9.9.9:17892")
        self.assertEqual(read_probe_url(updated), "http://10.9.9.9:17892")
        self.assertIn("name: study\n", updated)


class SaveTest(unittest.TestCase):
    def test_save_restarts_loaded_hub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            path.write_text("token: secret-token\nprobe:\n  url: http://10.0.0.8:17892\n", encoding="utf-8")
            calls: list[list[str]] = []

            def run(args: list[str], timeout: float = 20):
                calls.append(args)
                if args[1] == "print" and args[2] == "user/501/cn.weiekko.dock-hub":
                    return _done(0, "state = running\npid = 9\n")
                if args[1] == "print":
                    return _done(1, "Could not find service")
                return _done(0, "")

            ctl = Controller(
                repo=Path("/Users/weiekko/dock"),
                home=Path(tmp),
                uid=501,
                run=run,
                probe=lambda url: (False, None, "down"),
                hub_yaml=path,
            )
            result = ctl.set_windows_address("10.4.5.6")
            self.assertTrue(result["ok"])
            self.assertEqual(result["url"], "http://10.4.5.6:17892")
            self.assertEqual(read_probe_url(path.read_text(encoding="utf-8")), result["url"])
            self.assertIn("secret-token", path.read_text(encoding="utf-8"))
            self.assertIn(["launchctl", "kickstart", "-k", "user/501/cn.weiekko.dock-hub"], calls)

    def test_same_address_does_not_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            path.write_text("probe:\n  url: http://10.4.5.6:17892\n", encoding="utf-8")
            calls: list[list[str]] = []

            def run(args: list[str], timeout: float = 20):
                calls.append(list(args))
                return _done(0, "")

            ctl = Controller(
                repo=Path("/Users/weiekko/dock"),
                home=Path(tmp),
                uid=501,
                run=run,
                hub_yaml=path,
            )
            result = ctl.set_windows_address("10.4.5.6")
            self.assertTrue(result["ok"])
            self.assertEqual(calls, [])


class RouteTest(unittest.TestCase):
    def test_post_windows_address(self) -> None:
        seen: dict[str, str] = {}

        class Fake:
            def snapshot(self):
                return [{
                    "id": "hub",
                    "name": "Hub",
                    "blurb": "",
                    "state_label": "运行中",
                    "port": 17890,
                    "windows": {"url": "http://10.0.0.8:17892", "online": False, "label": "探针没响应"},
                }]

            def set_windows_address(self, address: str):
                seen["address"] = address
                return {"ok": True, "url": "http://10.4.5.6:17892", "message": "已改成 http://10.4.5.6:17892，Hub 已重启"}

            def apply(self, service_id: str, action: str):
                raise AssertionError("not this route")

            def apply_all(self, action: str):
                raise AssertionError("not this route")

        httpd = serve(Fake(), host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        raw = json.dumps({"address": "10.4.5.6"}).encode()
        req = urllib.request.Request(
            base + "/api/config/windows",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(req, timeout=2).read())
        self.assertEqual(seen["address"], "10.4.5.6")
        self.assertIn("已重启", body["message"])


def _done(code: int, out: str):
    class Result:
        returncode = code
        stdout = out
        stderr = "" if code == 0 else out

    return Result()


if __name__ == "__main__":
    unittest.main()
