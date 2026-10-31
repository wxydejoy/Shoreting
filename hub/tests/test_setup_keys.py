"""三端配置页写回 hub.yaml 的几条规矩。

配置页现在能改 Windows 探针、Mini 监控、三端地址簿和 host，写的是整份 yaml。
这里盯四件事：

1. 表单只提交一部分时，向导里没有的段落（dusk / guest / schedules…）不能被洗掉；
2. 地址统一成 http://host:port，主机名（mini.lan）和 IP 都认；
3. 口令留空 = 保持原值，浏览器拿不到原文；
4. 没提交 devices 时设备清单原样保留。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.setup_ui import _payload_to_raw


def _cfg(**extra):
    raw = {
        "name": "study",
        "token": "secret-token-value",
        "host": "0.0.0.0",
        "port": 17890,
        "devices": [
            {"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯", "on_prop": "on-2"},
            {"id": "steam", "name": "Steam", "type": "action", "run": {"program": "steam.exe"}},
        ],
        "dusk": {"device": "lamp", "until": "22:00", "brightness": 40},
        "guest": {"hours": 4},
        "schedules": [{"id": "morning", "at": "07:30", "on": ["lamp"]}],
        "mini": {"enabled": True, "base_url": "http://127.0.0.1:17891", "token": "mini-secret"},
        "probe": {"url": "http://win.lan:17892", "token": "probe-secret"},
        "companion": {
            "enabled": True,
            "llm": {"base_url": "https://llm.example/v1", "model": "m", "api_key": "sk-abc"},
            "tts": {"base_url": "http://127.0.0.1:18100", "token": "tts-secret"},
        },
    }
    raw.update(extra)
    return parse_config(raw)


class NodesConfigTest(unittest.TestCase):
    def test_defaults_are_domains(self) -> None:
        cfg = _cfg()
        self.assertEqual(cfg.nodes.mac.host, "mini.lan")
        self.assertEqual(cfg.nodes.win.host, "win.lan")
        self.assertEqual(cfg.nodes.android.host, "k20.lan")
        self.assertEqual(cfg.nodes.mac.url(), "http://mini.lan:17890")
        self.assertEqual(cfg.nodes.win.url(), "http://win.lan:17892")

    def test_partial_override_keeps_other_fields(self) -> None:
        cfg = _cfg(nodes={"win": {"ip": "10.0.0.9"}})
        self.assertEqual(cfg.nodes.win.ip, "10.0.0.9")
        self.assertEqual(cfg.nodes.win.host, "win.lan")
        self.assertEqual(cfg.nodes.win.port, 17892)

    def test_round_trip(self) -> None:
        cfg = _cfg(nodes={"android": {"host": "k20.lan", "port": 0}})
        raw = hub_config_to_raw(cfg)["nodes"]
        self.assertEqual(raw["android"]["host"], "k20.lan")
        self.assertNotIn("port", raw["android"])
        self.assertEqual(parse_config(hub_config_to_raw(cfg)).nodes.android.host, "k20.lan")


class PayloadTest(unittest.TestCase):
    def test_unlisted_sections_survive(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw({"nodes": {"win": {"host": "win.lan"}}}, cfg)
        self.assertIn("dusk", raw)
        self.assertIn("guest", raw)
        self.assertEqual(raw["schedules"][0]["id"], "morning")
        self.assertEqual(raw["companion"]["llm"]["api_key"], "sk-abc")

    def test_probe_is_normalized(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw(
            {"probe": {"url": "win.lan", "hub_url": "https://mini.lan:17890", "stale_ms": "8000"}},
            cfg,
        )
        self.assertEqual(raw["probe"]["url"], "http://win.lan:17892")
        self.assertEqual(raw["probe"]["hub_url"], "https://mini.lan:17890")
        self.assertEqual(raw["probe"]["stale_ms"], 8000)
        self.assertEqual(parse_config(raw).probe.url, "http://win.lan:17892")

    def test_bad_probe_is_rejected(self) -> None:
        cfg = _cfg()
        for bad in ("http://", "win lan", "ftp://win.lan:17892", "http://u:p@win.lan:17892"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _payload_to_raw({"probe": {"url": bad}}, cfg)

    def test_empty_probe_url_means_no_probe(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw({"probe": {"url": ""}}, cfg)
        self.assertEqual(raw["probe"]["url"], "")

    def test_blank_tokens_keep_current(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw(
            {"probe": {"token": ""}, "mini": {"token": ""}, "companion": {"tts": {"token": ""}}},
            cfg,
        )
        self.assertEqual(raw["probe"]["token"], "probe-secret")
        self.assertEqual(raw["mini"]["token"], "mini-secret")
        self.assertEqual(raw["companion"]["tts"]["token"], "tts-secret")

    def test_devices_omitted_keep_list(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw({"probe": {"url": "win.lan"}}, cfg)
        self.assertEqual([item["id"] for item in raw["devices"]], ["lamp", "steam"])
        self.assertEqual(raw["devices"][0]["on_prop"], "on-2")

    def test_mini_disable_and_host(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw({"mini": {"enabled": False}}, cfg)
        self.assertEqual(raw["mini"], {"enabled": False})
        raw = _payload_to_raw({"mini": {"base_url": "mini.lan:17891", "pc_ms": "500"}}, cfg)
        self.assertEqual(raw["mini"]["base_url"], "http://mini.lan:17891")
        self.assertEqual(raw["mini"]["pc_ms"], 500)

    def test_media_toggle(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw({"media": {"enabled": False}}, cfg)
        self.assertEqual(raw["media"], {"enabled": False})

    def test_host_and_port_validation(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw({"host": "127.0.0.1", "port": 17990}, cfg)
        self.assertEqual(raw["host"], "127.0.0.1")
        self.assertEqual(raw["port"], 17990)
        with self.assertRaises(ValueError):
            _payload_to_raw({"host": "0.0.0.0 # oops"}, cfg)
        with self.assertRaises(ValueError):
            _payload_to_raw({"port": 70000}, cfg)

    def test_nodes_round_trip_into_config(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw(
            {
                "nodes": {
                    "mac": {"host": "mini.lan", "ip": "10.14.14.117", "port": "17890"},
                    "win": {"host": "win.lan", "ip": "", "port": "17892"},
                }
            },
            cfg,
        )
        again = parse_config(raw)
        self.assertEqual(again.nodes.win.host, "win.lan")
        self.assertEqual(again.nodes.win.ip, "")
        self.assertEqual(again.nodes.mac.port, 17890)

    def test_payload_does_not_need_config_path(self) -> None:
        cfg = _cfg()
        cfg.path = Path("/tmp/does-not-exist/hub.yaml")
        raw = _payload_to_raw({"nodes": {"mac": {"host": "mini.lan"}}}, cfg)
        self.assertEqual(raw["nodes"]["mac"]["host"], "mini.lan")


if __name__ == "__main__":
    unittest.main()
