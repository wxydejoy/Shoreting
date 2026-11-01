"""单页壳：js/css 地址带构建时间戳，换版能穿透手机缓存。"""

from __future__ import annotations

import sys
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dock_console.server import asset_stamp, serve, versioned_shell

SHELL = (
    '<link rel="stylesheet" href="/assets/console.css" />'
    '<script src="/assets/console.js" defer></script>'
)


class VersionedShellTest(unittest.TestCase):
    def test_stamp_lands_on_both_assets(self) -> None:
        out = versioned_shell(SHELL, 1_700_000_000)
        self.assertIn("/assets/console.js?v=1700000000", out)
        self.assertIn("/assets/console.css?v=1700000000", out)

    def test_no_stamp_leaves_html_alone(self) -> None:
        self.assertEqual(versioned_shell(SHELL, 0), SHELL)

    def test_stamp_follows_build(self) -> None:
        # 有 assets 就一定有版本；没有就当 0（照旧发，不炸）
        self.assertGreaterEqual(asset_stamp(), 0)


class ShellEndpointTest(unittest.TestCase):
    def test_index_carries_version_and_no_store(self) -> None:
        httpd = serve(controller=None, host="127.0.0.1", port=0, token="secret")
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(base + "/", timeout=3) as resp:
                body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
                self.assertIn("岸亭控制台", body)
            self.assertIn("/assets/console.js?v=", body)
            self.assertIn("/assets/console.css?v=", body)

            # 带 ?v= 的请求照样能拿到资源（路由只看 path）
            with urllib.request.urlopen(
                base + f"/assets/console.js?v={asset_stamp()}", timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(
                    resp.headers.get("Content-Type"), "text/javascript; charset=utf-8"
                )
                self.assertTrue(resp.read(64))
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
