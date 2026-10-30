from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from dock_hub.config import LimitsConfig, hub_config_to_raw, parse_config
from dock_hub.ratelimit import MAX_KEYS, Gate, Limiter
from dock_hub.server import make_handler
from dock_hub.service import DockHub
from dock_hub.setup_ui import apply_save


class LimiterTest(unittest.TestCase):
    def test_allows_a_minute_worth_then_stops(self) -> None:
        limiter = Limiter(3)
        for _ in range(3):
            self.assertEqual(limiter.take("10.0.0.2", now=0.0), 0.0)
        self.assertGreater(limiter.take("10.0.0.2", now=0.0), 0.0)

    def test_refills_linearly(self) -> None:
        limiter = Limiter(60)  # 一秒一个
        for _ in range(60):
            limiter.take("a", now=0.0)
        self.assertGreater(limiter.take("a", now=0.5), 0.0)
        # 过了 1.5 秒，回填一个半，够扣一次
        self.assertEqual(limiter.take("a", now=1.5), 0.0)

    def test_refill_caps_at_capacity(self) -> None:
        limiter = Limiter(2)
        limiter.take("a", now=0.0)
        limiter.take("a", now=0.0)
        # 放一整天也只能攒回 2 个，不会攒出一百次爆发
        limiter.take("a", now=86400.0)
        limiter.take("a", now=86400.0)
        self.assertGreater(limiter.take("a", now=86400.0), 0.0)

    def test_wait_matches_the_deficit(self) -> None:
        limiter = Limiter(60)  # 每秒一个
        for _ in range(60):
            limiter.take("a", now=0.0)
        # 一点都没有了，要等满一秒
        self.assertAlmostEqual(limiter.take("a", now=0.0), 1.0, places=6)

    def test_keys_are_independent(self) -> None:
        limiter = Limiter(1)
        self.assertEqual(limiter.take("phone", now=0.0), 0.0)
        self.assertGreater(limiter.take("phone", now=0.0), 0.0)
        # 一个人刷爆不该影响另一个人
        self.assertEqual(limiter.take("tablet", now=0.0), 0.0)

    def test_burst_can_exceed_per_minute(self) -> None:
        limiter = Limiter(2, burst=5)
        for _ in range(5):
            self.assertEqual(limiter.take("a", now=0.0), 0.0)
        self.assertGreater(limiter.take("a", now=0.0), 0.0)

    def test_time_going_backwards_does_not_mint_tokens(self) -> None:
        # NTP 往回拨时 elapsed 会是负数；不夹住的话就成了无限额度
        limiter = Limiter(1)
        limiter.take("a", now=100.0)
        self.assertGreater(limiter.take("a", now=90.0), 0.0)

    def test_bookkeeping_is_bounded(self) -> None:
        limiter = Limiter(1)
        for index in range(MAX_KEYS + 200):
            limiter.take(f"10.0.0.{index}", now=float(index))
        self.assertLessEqual(len(limiter), MAX_KEYS + 200)

    def test_rejects_nonsense_rate(self) -> None:
        with self.assertRaises(ValueError):
            Limiter(0)


class GateTest(unittest.TestCase):
    def test_disabled_config_gives_no_gate(self) -> None:
        self.assertIsNone(Gate.from_config(LimitsConfig(enabled=False)))

    def test_zero_rate_means_off(self) -> None:
        gate = Gate.from_config(LimitsConfig(chat_per_min=0))
        assert gate is not None
        self.assertNotIn("chat", gate)
        self.assertEqual(gate.check("chat", "1.2.3.4"), 0.0)

    def test_endpoints_do_not_share_quota(self) -> None:
        gate = Gate.from_config(LimitsConfig(chat_per_min=1, command_per_min=1))
        assert gate is not None
        self.assertEqual(gate.check("chat", "a"), 0.0)
        self.assertGreater(gate.check("chat", "a"), 0.0)
        # 聊天被拦不代表灯也按不动
        self.assertEqual(gate.check("command", "a"), 0.0)

    def test_unknown_bucket_is_unlimited(self) -> None:
        gate = Gate.from_config(LimitsConfig())
        assert gate is not None
        for _ in range(100):
            self.assertEqual(gate.check("snapshot", "a"), 0.0)


class LimitsConfigTest(unittest.TestCase):
    BASE = {"name": "study", "token": "secret-token-value", "host": "127.0.0.1", "port": 0}

    def test_defaults_when_absent(self) -> None:
        cfg = parse_config(dict(self.BASE))
        self.assertTrue(cfg.limits.enabled)
        self.assertEqual(cfg.limits.chat_per_min, 20)
        self.assertEqual(cfg.limits.command_per_min, 60)

    def test_false_turns_it_off(self) -> None:
        cfg = parse_config({**self.BASE, "limits": False})
        self.assertFalse(cfg.limits.enabled)

    def test_partial_section_keeps_defaults(self) -> None:
        cfg = parse_config({**self.BASE, "limits": {"chat_per_min": 5}})
        self.assertEqual(cfg.limits.chat_per_min, 5)
        self.assertEqual(cfg.limits.announce_per_min, 10)

    def test_negative_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config({**self.BASE, "limits": {"chat_per_min": -1}})

    def test_not_a_dict_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config({**self.BASE, "limits": 20})

    def test_round_trip_keeps_custom_values(self) -> None:
        cfg = parse_config({**self.BASE, "limits": {"chat_per_min": 7, "login_per_min": 3}})
        raw = hub_config_to_raw(cfg)
        self.assertEqual(raw["limits"]["chat_per_min"], 7)
        self.assertEqual(raw["limits"]["login_per_min"], 3)
        again = parse_config(raw)
        self.assertEqual(again.limits.chat_per_min, 7)
        self.assertEqual(again.limits.login_per_min, 3)

    def test_round_trip_stays_quiet_by_default(self) -> None:
        # 默认值不该被写进每个人的 yaml，那会让 diff 里多一行看不懂的东西
        cfg = parse_config(dict(self.BASE))
        self.assertNotIn("limits", hub_config_to_raw(cfg))


class WizardKeepsLimitsTest(unittest.TestCase):
    """向导表单里没有限流那几格，点一次保存不能把用户手写的 limits 抹掉。"""

    def test_save_from_wizard_keeps_custom_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            cfg = parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "host": "127.0.0.1",
                    "port": 17890,
                    "limits": {"chat_per_min": 3},
                },
                path,
            )
            hub = DockHub(cfg)
            apply_save(hub, {"name": "study", "token": "secret-token-value", "port": 17890})
            saved = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["limits"]["chat_per_min"], 3)
            # 顺手确认写回的值还能再读成配置（别只对了一半）
            self.assertEqual(parse_config(saved, path).limits.chat_per_min, 3)


class HttpLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
                "mini": False,
                "limits": {"chat_per_min": 2, "command_per_min": 50},
            }
        )
        self.hub = DockHub(cfg)
        handler = make_handler(self.hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _post(self, path: str, body: dict) -> tuple[int, dict, dict]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        raw = json.dumps(body).encode("utf-8")
        conn.request(
            "POST",
            path,
            body=raw,
            headers={
                "Authorization": "Bearer secret-token-value",
                "Content-Type": "application/json",
            },
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        headers = dict(resp.getheaders())
        conn.close()
        return resp.status, payload, headers

    def test_chat_gets_429_with_retry_after(self) -> None:
        answer = {"reply": "嗯", "audio_id": None}
        with patch.object(self.hub, "companion_chat", return_value=answer):
            for _ in range(2):
                status, _body, _headers = self._post("/v1/companion/chat", {"text": "在吗"})
                self.assertEqual(status, 200)
            status, body, headers = self._post("/v1/companion/chat", {"text": "在吗"})
        self.assertEqual(status, 429)
        self.assertEqual(body["error"]["code"], "too_many")
        self.assertIn("秒后再试", body["error"]["message"])
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)

    def test_local_client_is_limited_too(self) -> None:
        # 测试连接本身就来自 127.0.0.1；本机免鉴权，但不免限流
        with patch.object(self.hub, "companion_chat", return_value={"reply": "嗯"}):
            self._post("/v1/companion/chat", {"text": "1"})
            self._post("/v1/companion/chat", {"text": "2"})
            status, _body, _headers = self._post("/v1/companion/chat", {"text": "3"})
        self.assertEqual(status, 429)

    def test_other_endpoints_are_not_collateral(self) -> None:
        with patch.object(self.hub, "companion_chat", return_value={"reply": "嗯"}):
            for _ in range(3):
                self._post("/v1/companion/chat", {"text": "在吗"})
        # 聊天额度用完了，但设备控制照样能用
        status, _body, _headers = self._post("/v1/devices/nope/command", {"on": True})
        self.assertEqual(status, 404)

    def test_switching_limits_off_removes_the_gate(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "limits": False,
            }
        )
        self.assertIsNone(DockHub(cfg).gate)


if __name__ == "__main__":
    unittest.main()
