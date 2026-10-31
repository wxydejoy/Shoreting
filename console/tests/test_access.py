"""控制台局域网门禁：本机放行，局域网凭 ?key= / Cookie / Bearer。"""

from __future__ import annotations

import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dock_console.server import _cookie_key, check_access, read_hub_token, serve

TOKEN = "tok-abc-123"


class CheckAccessTest(unittest.TestCase):
    def test_local_bypasses_even_without_token(self) -> None:
        for ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(check_access(ip, None))
            self.assertTrue(check_access(ip, TOKEN, query_key="wrong"))

    def test_lan_rejected_without_token_or_with_wrong_key(self) -> None:
        self.assertFalse(check_access("10.14.14.170", None))
        self.assertFalse(check_access("10.14.14.170", None, query_key=TOKEN))
        self.assertFalse(check_access("10.14.14.170", TOKEN))
        self.assertFalse(check_access("10.14.14.170", TOKEN, query_key="tok-abc-124"))
        self.assertFalse(check_access("10.14.14.170", TOKEN, cookie="key=tok-abc-124"))

    def test_lan_accepted_by_query_cookie_bearer(self) -> None:
        self.assertTrue(check_access("10.14.14.170", TOKEN, query_key=TOKEN))
        self.assertTrue(check_access("10.14.14.170", TOKEN, cookie=f"key={TOKEN}"))
        self.assertTrue(check_access("10.14.14.170", TOKEN, authorization=f"Bearer {TOKEN}"))

    def test_empty_credential_never_matches(self) -> None:
        self.assertFalse(check_access("10.14.14.170", TOKEN, query_key=""))
        self.assertFalse(check_access("10.14.14.170", TOKEN, cookie="key="))
        self.assertFalse(check_access("10.14.14.170", TOKEN, authorization="Bearer "))


class CookieKeyTest(unittest.TestCase):
    def test_reads_key_among_others(self) -> None:
        self.assertEqual(_cookie_key("a=1; key=tok; b=2"), "tok")
        self.assertEqual(_cookie_key("key=tok"), "tok")

    def test_missing_key(self) -> None:
        self.assertEqual(_cookie_key("a=1"), "")
        self.assertEqual(_cookie_key(""), "")


class ReadHubTokenTest(unittest.TestCase):
    def test_top_level_token_only(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("token: 'tok-top'\nprobe:\n  token: tok-nested\n")
            path = Path(fh.name)
        try:
            self.assertEqual(read_hub_token(path), "tok-top")
        finally:
            path.unlink()

    def test_missing_file(self) -> None:
        self.assertEqual(read_hub_token(Path("/nonexistent/hub.yaml")), "")


class HealthEndpointTest(unittest.TestCase):
    def test_health_open(self) -> None:
        httpd = serve(controller=None, host="127.0.0.1", port=0, token="secret")
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        import threading

        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b"dock-console", resp.read())
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
