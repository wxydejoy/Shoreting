"""电脑电源（F21）：关机 / 睡眠 / 锁屏，两步确认是死规矩。

测三处：听懂（infer_power）、探针侧执行（power_command + /v1/power）、
伴侣侧的两步确认（问 → 确认才动手）。确认过期、答非所问、换动作，
都在伴侣侧的测试里盯死。
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from dock_hub.companion import Companion, POWER_TTL_SEC
from dock_hub.config import CompanionConfig, parse_config
from dock_hub.errors import HubError
from dock_hub.intents import infer_power
from dock_hub.probe import power_command


class InferPowerTest(unittest.TestCase):
    def test_shutdown(self) -> None:
        for text in ("关电脑", "关掉电脑", "把电脑关了", "电脑关机", "帮我关机吧", "关机"):
            with self.subTest(text=text):
                self.assertEqual(infer_power(text), "shutdown")

    def test_sleep(self) -> None:
        for text in ("让电脑睡会儿", "电脑睡一会", "电脑休眠", "电脑睡眠", "电脑待机"):
            with self.subTest(text=text):
                self.assertEqual(infer_power(text), "sleep")

    def test_lock(self) -> None:
        for text in ("锁屏", "锁个屏", "锁定电脑", "把屏幕锁了", "锁电脑"):
            with self.subTest(text=text):
                self.assertEqual(infer_power(text), "lock")

    def test_negatives(self) -> None:
        # 「睡前」是场景、「关灯」是灯、「别关机」是拒绝——一个都不能抢
        for text in ("睡前", "睡前模式", "关灯", "亮一点", "今天好累", "别关机", "开黑", "关掉灯带"):
            with self.subTest(text=text):
                self.assertIsNone(infer_power(text))


class PowerCommandTest(unittest.TestCase):
    def test_shapes(self) -> None:
        self.assertEqual(power_command("lock"), ["rundll32.exe", "user32.dll,LockWorkStation"])
        self.assertEqual(power_command("sleep"), ["rundll32.exe", "powrprof.dll,SetSuspendState 0,1,0"])
        self.assertEqual(power_command("shutdown"), ["shutdown.exe", "/s", "/t", "5"])

    def test_unknown_action_rejected(self) -> None:
        with self.assertRaises(HubError) as ctx:
            power_command("reboot")
        self.assertEqual(ctx.exception.code, "bad_request")


class ProbePowerEndpointTest(unittest.TestCase):
    def test_handle_power_dispatches(self) -> None:
        from dock_hub.probe import handle_power

        cfg = parse_config({"name": "study", "token": "t", "mini": False})
        with patch("dock_hub.probe.execute_power") as fired:
            result = handle_power(cfg, {"power": "lock"})
        fired.assert_called_once_with("lock")
        self.assertEqual(result, {"ok": True, "power": "lock"})

    def test_handle_power_rejects_unknown(self) -> None:
        from dock_hub.probe import handle_power

        cfg = parse_config({"name": "study", "token": "t", "mini": False})
        # 不打桩：坏动作要在碰电源之前就被 power_command 拦下来
        with self.assertRaises(HubError) as ctx:
            handle_power(cfg, {"power": "format"})
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_route_serves_power_with_auth(self) -> None:
        from dock_hub.probe import make_probe_handler

        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "probe": {"hub_url": "http://127.0.0.1:9", "listen": "127.0.0.1:0"},
                "devices": [],
                "mini": False,
            }
        )
        with patch("dock_hub.probe.execute_power") as fired:
            handler = make_probe_handler(cfg)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]

            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/v1/power", body=b'{"power": "lock"}', headers={})
            resp = conn.getresponse()
            conn.close()
            self.assertEqual(resp.status, 401)
            fired.assert_not_called()

            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/power",
                body=json.dumps({"power": "sleep"}).encode("utf-8"),
                headers={"Authorization": "Bearer secret-token-value"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertEqual(body, {"ok": True, "power": "sleep"})
            fired.assert_called_once_with("sleep")


class ServicePowerTest(unittest.TestCase):
    def _cfg(self, **extra):
        raw = {
            "name": "study",
            "token": "secret-token-value",
            "devices": [],
            "mini": False,
        }
        raw.update(extra)
        return parse_config(raw)

    def test_needs_probe(self) -> None:
        from dock_hub.service import DockHub

        hub = DockHub(self._cfg())
        self.addCleanup(hub.reporter.stop)
        with self.assertRaises(HubError) as ctx:
            hub.power_action("shutdown")
        self.assertEqual(ctx.exception.code, "offline")

    def test_posts_to_probe(self) -> None:
        from dock_hub.service import DockHub

        hub = DockHub(self._cfg(probe={"url": "http://10.83.22.31:17892"}))
        self.addCleanup(hub.reporter.stop)
        captured: dict = {}

        resp = json.dumps({"ok": True, "power": "shutdown"}).encode("utf-8")

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return resp

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["auth"] = req.headers.get("Authorization")
            return FakeResp()

        with patch("dock_hub.service.urllib.request.urlopen", side_effect=fake_urlopen):
            hub.power_action("shutdown")
        self.assertTrue(captured["url"].endswith("/v1/power"))
        self.assertEqual(captured["payload"], {"power": "shutdown"})
        self.assertEqual(captured["auth"], "Bearer secret-token-value")

    def test_rejects_unknown_action(self) -> None:
        from dock_hub.service import DockHub

        hub = DockHub(self._cfg(probe={"url": "http://10.83.22.31:17892"}))
        self.addCleanup(hub.reporter.stop)
        with self.assertRaises(HubError) as ctx:
            hub.power_action("reboot")
        self.assertEqual(ctx.exception.code, "bad_request")


SKILLS = [
    {"id": "lamp", "name": "台灯", "kind": "toggle"},
    {"id": "desk-led", "name": "灯带", "kind": "toggle"},
]


class CompanionPowerTest(unittest.TestCase):
    """两步确认的整条流程，全程不碰 LLM：问 → 确认 → 动手 / 反悔。"""

    def _companion(self, fired: list[str]) -> Companion:
        comp = Companion(CompanionConfig(enabled=True))

        def power(action: str) -> None:
            fired.append(action)

        comp.set_skills([dict(item) for item in SKILLS], lambda ident, on, **_: None,
                        world={"power": power})
        return comp

    def test_ask_then_confirm_fires(self) -> None:
        fired: list[str] = []
        comp = self._companion(fired)
        asked = comp.chat({"text": "关电脑", "id": "t1"})
        self.assertIn("确认", asked["text"])
        self.assertEqual(fired, [])
        done = comp.chat({"text": "确认", "id": "t2"})
        self.assertEqual(done["text"], "好，五秒后关机。")
        self.assertEqual(fired, ["shutdown"])

    def test_repeat_same_action_counts_as_confirm(self) -> None:
        fired: list[str] = []
        comp = self._companion(fired)
        comp.chat({"text": "锁屏", "id": "t1"})
        done = comp.chat({"text": "锁屏", "id": "t2"})
        self.assertEqual(done["text"], "锁了。")
        self.assertEqual(fired, ["lock"])

    def test_cancel_spares_the_machine(self) -> None:
        fired: list[str] = []
        comp = self._companion(fired)
        comp.chat({"text": "关电脑", "id": "t1"})
        self.assertEqual(comp.chat({"text": "算了", "id": "t2"})["text"], "好，不动了。")
        self.assertEqual(fired, [])
        self.assertIsNone(comp._pending_power)

    def test_switching_action_reasks(self) -> None:
        fired: list[str] = []
        comp = self._companion(fired)
        comp.chat({"text": "关电脑", "id": "t1"})
        asked = comp.chat({"text": "还是锁屏吧", "id": "t2"})
        self.assertIn("锁屏", asked["text"])
        self.assertEqual(fired, [])
        self.assertEqual(comp._pending_power[0], "lock")

    def test_expired_confirm_does_not_fire(self) -> None:
        fired: list[str] = []
        comp = self._companion(fired)
        comp.chat({"text": "关电脑", "id": "t1"})
        assert comp._pending_power is not None
        action, _ = comp._pending_power
        comp._pending_power = (action, time.monotonic() - POWER_TTL_SEC - 1)
        # 过期之后再说「关电脑」：重新问，而不是当成重复确认直接动手
        asked = comp.chat({"text": "关电脑", "id": "t2"})
        self.assertIn("确认", asked["text"])
        self.assertEqual(fired, [])

    def test_bare_yes_confirms_but_sentences_with_yes_do_not(self) -> None:
        fired: list[str] = []
        comp = self._companion(fired)
        comp.chat({"text": "锁屏", "id": "t1"})
        self.assertEqual(comp.chat({"text": "好", "id": "t2"})["text"], "锁了。")
        self.assertEqual(fired, ["lock"])

        comp.chat({"text": "关电脑", "id": "t3"})
        # 「今天好累」里有「好」，但那不是答应——掉手，当没问过
        result = comp._handle_power("今天好累", "t4", comp._pop_power())
        self.assertIsNone(result)
        self.assertEqual(fired, ["lock"])

    def test_unrelated_reply_drops_pending(self) -> None:
        fired: list[str] = []
        comp = self._companion(fired)
        comp.chat({"text": "关电脑", "id": "t1"})
        self.assertIsNotNone(comp._pending_power)
        # 问着的时候说别的：当没问过，别让「确认」隔山打牛
        result = comp._handle_power("今天好累", "t2", comp._pop_power())
        self.assertIsNone(result)
        self.assertIsNone(comp._pending_power)

    def test_failure_reports(self) -> None:
        comp = Companion(CompanionConfig(enabled=True))

        def power(action: str) -> None:
            raise HubError("offline", "电脑未开机或探针无响应")

        comp.set_skills([dict(item) for item in SKILLS], lambda ident, on, **_: None,
                        world={"power": power})
        comp.chat({"text": "关电脑", "id": "t1"})
        done = comp.chat({"text": "确认", "id": "t2"})
        self.assertEqual(done["text"], "关机没动成。")

    def test_without_power_callback_does_not_ask(self) -> None:
        # 没接探针就不认电源意图：问了也是空头支票，宁可沉默交给后面
        comp = Companion(CompanionConfig(enabled=True))
        comp.set_skills([dict(item) for item in SKILLS], lambda ident, on, **_: None)
        self.assertIsNone(comp._handle_power("关电脑", "t1", None))


if __name__ == "__main__":
    unittest.main()
