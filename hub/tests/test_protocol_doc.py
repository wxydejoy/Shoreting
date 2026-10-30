"""协议文档和代码对账。

这份测试守的是「文档会不会悄悄过期」：server.py 加了一条路由、openapi.yaml 忘了补，
以前只能靠人记得，现在这里会红。

被测的是 scripts/check_protocol.py —— 同一份逻辑既能在 CI 里跑（`python3
scripts/check_protocol.py`），也能在本地随手跑，所以测试直接调它的函数，不复制规则。
"""

from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_protocol.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_protocol", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckerParsingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checker = load_checker()

    def test_reads_both_literal_and_variable_prefixes(self) -> None:
        source = '''
        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if path == "/v1/plain":
                    pass
                if path.startswith("/v1/literal/"):
                    pass
                books_prefix = "/v1/books/"
                if path.startswith(books_prefix):
                    pass

            def do_POST(self) -> None:
                if path == "/v1/plain":
                    pass
        '''
        routes = self.checker.code_routes(source)
        found = {(method, path) for method, path, _ in routes}
        self.assertIn(("GET", "/v1/plain"), found)
        self.assertIn(("POST", "/v1/plain"), found)
        self.assertIn(("GET", "/v1/literal"), found)
        # 变量里藏的前缀，靠赋值语句反推出来
        self.assertIn(("GET", "/v1/books"), found)

    def test_prefix_covers_a_template_with_params_only(self) -> None:
        documented = {"/v1/books/{id}/{action}", "/v1/books/note"}
        self.assertTrue(self.checker.covered("/v1/books", True, documented))
        # 文档里只有一条具体子路径、不是模板，说明不了「整片前缀都实现了」
        self.assertFalse(self.checker.covered("/v1/shelf", True, {"/v1/shelf/note"}))

    def test_reads_openapi_paths_without_yaml(self) -> None:
        text = """
paths:
  /health:
    get:
      summary: ping
  /v1/thing/{id}:
    post:
      summary: do
security:
  - bearer: []
"""
        self.assertEqual(
            self.checker.documented_routes(text),
            [("GET", "/health"), ("POST", "/v1/thing/{id}")],
        )


class RepoStaysInSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = load_checker()

    def test_openapi_is_valid_yaml(self) -> None:
        # 之前这里有一行没加引号的 description，整个文件解析不了，
        # 谁也没发现，因为没人真的用机器读过它。
        import yaml

        spec = yaml.safe_load((REPO / "docs" / "openapi.yaml").read_text(encoding="utf-8"))
        self.assertIn("/v1/snapshot", spec["paths"])
        mine = spec["paths"]["/v1/snapshot"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(mine["$ref"], "#/components/schemas/Snapshot")

    def test_every_served_route_is_documented(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.checker.main(["--root", str(REPO), "--quiet"])
        self.assertEqual(code, 0, out.getvalue())

    def test_missing_route_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hub" / "dock_hub").mkdir(parents=True)
            (root / "docs").mkdir()
            (root / "hub" / "dock_hub" / "server.py").write_text(
                'def do_GET(self):\n    if path == "/v1/known":\n        pass\n'
                '    if path == "/v1/ghost":\n        pass\n',
                encoding="utf-8",
            )
            (root / "docs" / "openapi.yaml").write_text(
                "paths:\n  /v1/known:\n    get:\n      summary: ok\n",
                encoding="utf-8",
            )
            out = io.StringIO()
            with redirect_stdout(out):
                code = self.checker.main(["--root", str(root)])
            self.assertEqual(code, 1)
            self.assertIn("/v1/ghost", out.getvalue())


if __name__ == "__main__":
    unittest.main()
