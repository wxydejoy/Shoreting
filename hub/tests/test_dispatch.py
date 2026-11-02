"""泰缇斯派发（F40）：派一件事、查状态、超时收尸、重启收尾。

一律用 ``/bin/echo`` 这类不花钱的 runner 验链路——真跑 dsh 需要模型凭据，
那是上线前的人工前提，不该让代码验收依赖它。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from dock_hub.config import parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub

AUTH = {"Authorization": "Bearer secret-token-value"}
# 一定睡过头的 runner：用来验超时与重启收尸（必须有 {task} 占位）
SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)", "{task}"]


class DispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.port = 0

    def _start(self, dispatch: dict | None = None) -> DockHub:
        """起一个真的 HTTP 服务。dispatch=None 表示 yaml 里没这一段（关着）。"""

        raw: dict = {
            "name": "study",
            "token": "secret-token-value",
            "host": "127.0.0.1",
            "port": 0,
            "devices": [],
            "mini": False,
        }
        if dispatch is not None:
            raw["dispatch"] = dispatch
        hub = DockHub(parse_config(raw, self.tmp / "hub.yaml"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        return hub

    def _hub(self, dispatch: dict, *, sweep: bool = False) -> DockHub:
        """同一个数据目录再起一个 hub（模拟重启），不占端口。"""

        hub = DockHub(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "host": "127.0.0.1",
                    "port": 0,
                    "devices": [],
                    "mini": False,
                    "dispatch": dispatch,
                },
                self.tmp / "hub.yaml",
            )
        )
        if sweep:
            hub.sweep_dispatch()
        return hub

    def _get(self, path: str, headers: dict[str, str] | None = None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def _post(self, path: str, payload: dict, headers: dict[str, str] | None = None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            body = json.dumps(payload).encode("utf-8")
            sent = {"Content-Type": "application/json", **(headers or {})}
            conn.request("POST", path, body=body, headers=sent)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def _wait(self, task_id: str, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            _, last = self._get(f"/v1/dispatch/status?task_id={task_id}", AUTH)
            if last.get("status") in ("done", "failed", "timeout", "killed"):
                return last
            time.sleep(0.05)
        self.fail(f"任务没收尾：{last}")

    def test_dispatch_returns_202_and_task_id(self) -> None:
        self._start({"runner": ["/bin/echo", "{task}"]})
        status, payload = self._post("/v1/dispatch", {"task": "只回复 OK"}, AUTH)
        self.assertEqual(status, 202)
        self.assertTrue(payload["task_id"].startswith("d-"))
        self.assertEqual(payload["status"], "queued")

    def test_dispatch_without_auth_returns_401(self) -> None:
        self._start({"runner": ["/bin/echo", "{task}"]})
        status, payload = self._post("/v1/dispatch", {"task": "只回复 OK"})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")

    def test_status_unknown_task_id_returns_404(self) -> None:
        self._start({"runner": ["/bin/echo", "{task}"]})
        status, payload = self._get("/v1/dispatch/status?task_id=d-nope", AUTH)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_empty_task_returns_400(self) -> None:
        self._start({"runner": ["/bin/echo", "{task}"]})
        status, payload = self._post("/v1/dispatch", {"task": "   "}, AUTH)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_echo_runner_reaches_done(self) -> None:
        self._start({"runner": ["/bin/echo", "{task}"]})
        _, created = self._post("/v1/dispatch", {"task": "把日志里的报错归类"}, AUTH)
        payload = self._wait(created["task_id"])
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["result"].strip(), "把日志里的报错归类")
        self.assertFalse(payload["result_truncated"])
        self.assertTrue(payload["started_at"])
        self.assertTrue(payload["finished_at"])

    def test_empty_stdout_marks_failed(self) -> None:
        self._start({"runner": ["/bin/true", "{task}"], "timeout_seconds": 10})
        _, created = self._post("/v1/dispatch", {"task": "什么都不说"}, AUTH)
        # 退 0 但一个字都没吐：dsh 缺 key 时就是这个样子，不能算成功
        self.assertEqual(self._wait(created["task_id"])["status"], "failed")

    def test_timeout_kills_process_group(self) -> None:
        self._start({"runner": SLEEPER, "timeout_seconds": 5})
        _, created = self._post("/v1/dispatch", {"task": "睡一觉"}, AUTH)
        started = time.monotonic()
        payload = self._wait(created["task_id"], timeout=15)
        self.assertEqual(payload["status"], "timeout")
        self.assertLess(time.monotonic() - started, 12)

    def test_result_truncated_at_max_chars(self) -> None:
        self._start(
            {
                "runner": [sys.executable, "-c", "print('a'*500)", "{task}"],
                "result_max_chars": 200,
            }
        )
        _, created = self._post("/v1/dispatch", {"task": "说很多"}, AUTH)
        payload = self._wait(created["task_id"])
        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["result_truncated"])
        self.assertLess(len(payload["result"]), 300)

    def test_status_survives_new_hub_instance(self) -> None:
        self._start({"runner": ["/bin/echo", "{task}"]})
        _, created = self._post("/v1/dispatch", {"task": "记一笔"}, AUTH)
        self._wait(created["task_id"])
        other = self._hub({"runner": ["/bin/echo", "{task}"]})
        self.assertEqual(other.dispatch_status(created["task_id"])["status"], "done")

    def test_sweep_marks_killed_after_restart(self) -> None:
        hub = self._start({"runner": SLEEPER, "timeout_seconds": 5})
        _, created = self._post("/v1/dispatch", {"task": "睡一觉"}, AUTH)
        deadline = time.monotonic() + 5
        record: dict = {}
        while time.monotonic() < deadline:
            record = hub.dispatch_store.get(created["task_id"]) or {}
            if record.get("status") == "running" and record.get("pid"):
                break
            time.sleep(0.02)
        self.assertTrue(record.get("pid"), "任务没跑到 running")
        other = self._hub({"runner": SLEEPER, "timeout_seconds": 5}, sweep=True)
        self.assertEqual(other.dispatch_status(created["task_id"])["status"], "killed")

    def test_dispatch_disabled_returns_404(self) -> None:
        self._start(None)
        status, payload = self._post("/v1/dispatch", {"task": "只回复 OK"}, AUTH)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_runner_without_placeholder_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "dispatch": {"runner": ["/bin/echo", "固定命令"]},
                },
                self.tmp / "hub.yaml",
            )


if __name__ == "__main__":
    unittest.main()
