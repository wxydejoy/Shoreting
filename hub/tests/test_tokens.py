from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub
from dock_hub.setup_ui import apply_save

OWNER = "owner-token-0123456789"
PHONE = "phone-token-abcdefgh"
TABLET = "tablet-token-zyxwvuts"

BASE = {
    "name": "study",
    "token": OWNER,
    "host": "127.0.0.1",
    "port": 0,
    "devices": [],
    "mini": False,
}


def config_with(*tokens: dict, **extra) -> dict:
    raw = dict(BASE, **extra)
    if tokens:
        raw["tokens"] = list(tokens)
    return raw


class ParseTokensTest(unittest.TestCase):
    def test_absent_means_only_owner(self) -> None:
        cfg = parse_config(dict(BASE))
        self.assertEqual(cfg.tokens, [])
        self.assertEqual(cfg.auth_values(), (OWNER,))

    def test_owner_first_then_devices(self) -> None:
        cfg = parse_config(
            config_with(
                {"id": "phone", "value": PHONE},
                {"id": "tablet", "value": TABLET},
            )
        )
        self.assertEqual(cfg.auth_values(), (OWNER, PHONE, TABLET))
        self.assertEqual(cfg.tokens[0].id, "phone")

    def test_optional_name(self) -> None:
        cfg = parse_config(config_with({"id": "phone", "value": PHONE, "name": "我的手机"}))
        self.assertEqual(cfg.tokens[0].name, "我的手机")

    def test_short_value_rejected(self) -> None:
        # 短 token 在局域网里等于没有
        with self.assertRaises(ValueError):
            parse_config(config_with({"id": "phone", "value": "1234567"}))

    def test_duplicate_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                config_with(
                    {"id": "phone", "value": PHONE},
                    {"id": "phone", "value": TABLET},
                )
            )

    def test_duplicate_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                config_with(
                    {"id": "phone", "value": PHONE},
                    {"id": "tablet", "value": PHONE},
                )
            )

    def test_cannot_reuse_owner_token(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(config_with({"id": "phone", "value": OWNER}))

    def test_bad_shape_rejected(self) -> None:
        for bad in ({"id": "phone", "value": PHONE}, [{"value": PHONE}], ["phone"]):
            with self.assertRaises(ValueError):
                parse_config(config_with() | {"tokens": bad})

    def test_round_trip(self) -> None:
        raw = config_with({"id": "phone", "value": PHONE, "name": "手机"})
        cfg = parse_config(raw)
        dumped = hub_config_to_raw(cfg)
        self.assertEqual(dumped["tokens"], [{"id": "phone", "value": PHONE, "name": "手机"}])
        again = parse_config(dumped)
        self.assertEqual(again.auth_values(), (OWNER, PHONE))

    def test_round_trip_quiet_without_tokens(self) -> None:
        self.assertNotIn("tokens", hub_config_to_raw(parse_config(dict(BASE))))


class AuthOverHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            config_with({"id": "phone", "value": PHONE}),
            Path(self.tmp.name) / "hub.yaml",
        )
        self.hub = DockHub(cfg)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def _get(self, path: str, token: str | None) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, body

    def test_owner_token_works(self) -> None:
        status, _body = self._get("/v1/snapshot", OWNER)
        self.assertEqual(status, 200)

    def test_device_token_works(self) -> None:
        status, _body = self._get("/v1/snapshot", PHONE)
        self.assertEqual(status, 200)

    def test_unknown_token_rejected(self) -> None:
        status, body = self._get("/v1/snapshot", "not-a-real-token-000")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_missing_header_rejected(self) -> None:
        status, _body = self._get("/v1/snapshot", None)
        self.assertEqual(status, 401)

    def test_bearer_prefix_is_case_insensitive(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/v1/snapshot", headers={"Authorization": f"bearer {PHONE}"})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_revoked_token_is_rejected(self) -> None:
        # 「吊销」就是把这个 id 从 yaml 里删掉，然后重载配置
        cfg = parse_config(BASE, Path(self.tmp.name) / "hub.yaml")
        self.hub.reload_config(cfg)
        status, _body = self._get("/v1/snapshot", PHONE)
        self.assertEqual(status, 401)


class WizardKeepsTokensTest(unittest.TestCase):
    def test_save_preserves_device_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            cfg = parse_config(config_with({"id": "phone", "value": PHONE}), path)
            hub = DockHub(cfg)
            apply_save(hub, {"name": "study", "token": OWNER, "port": 0})
            saved = parse_config(yaml.safe_load(path.read_text(encoding="utf-8")), path)
            self.assertEqual(saved.auth_values(), (OWNER, PHONE))


if __name__ == "__main__":
    unittest.main()
