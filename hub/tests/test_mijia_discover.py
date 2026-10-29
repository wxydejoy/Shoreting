from __future__ import annotations

import unittest
from unittest.mock import patch

from dock_hub.config import DeviceConfig, TemperatureConfig, parse_config
from dock_hub.mijia_bridge import (
    BoundDevice,
    MijiaSession,
    PropRef,
    bind_from_config,
    discover_switchable,
    query_params,
    _voice_device_id,
)
from dock_hub.service import DockHub


def _prop(name: str, typ: str = "bool", siid: int = 2, piid: int = 1) -> dict:
    return {"name": name, "type": typ, "method": {"siid": siid, "piid": piid}}


def _info(model: str, cache_path=None) -> dict:
    if model == "yeelight":
        return {"properties": [_prop("on"), _prop("brightness", "uint", 2, 2)]}
    if model == "humi":
        return {"properties": [_prop("on")]}
    if model == "plug":
        return {"properties": [_prop("switch-status")]}
    return {"properties": [_prop("temperature", "float", 3, 1)]}


class FakeApi:
    def __init__(self, listed: list[dict]) -> None:
        self._listed = listed

    def get_devices_list(self) -> list[dict]:
        return self._listed


class DiscoverTest(unittest.TestCase):
    def test_voice_id_is_url_safe(self) -> None:
        ident = _voice_device_id("blt.3.abc:def", set())
        self.assertTrue(ident.startswith("m"))
        self.assertNotIn(":", ident)

    def test_skips_screen_and_sensors(self) -> None:
        listed = {
            "台灯": {"did": "111", "model": "yeelight", "name": "台灯"},
            "加湿器": {"did": "222", "model": "humi", "name": "加湿器"},
            "温湿度计": {"did": "333", "model": "sensor", "name": "温湿度计"},
            "客厅插座": {"did": "444", "model": "plug", "name": "客厅插座"},
        }
        with patch("dock_hub.mijia_bridge.get_device_info", side_effect=_info):
            extras = discover_switchable(
                listed,
                taken_names={"台灯", "温湿度计"},
                taken_ids={"lamp"},
            )
        names = {item.cfg.name for item in extras}
        self.assertEqual(names, {"加湿器", "客厅插座"})
        self.assertTrue(all(item.voice_only for item in extras))

    def test_bind_adds_extras_without_polling_them(self) -> None:
        lamp = DeviceConfig(id="lamp", name="台灯", type="light", mijia_name="台灯")
        temp = TemperatureConfig(id="desk", name="书桌", mijia_name="温湿度计")
        api = FakeApi(
            [
                {"name": "台灯", "did": "111", "model": "yeelight"},
                {"name": "加湿器", "did": "222", "model": "humi"},
                {"name": "温湿度计", "did": "333", "model": "sensor"},
            ]
        )
        with patch("dock_hub.mijia_bridge.get_device_info", side_effect=_info):
            session = bind_from_config(api, [lamp], temp)
        self.assertIn("lamp", session.devices)
        extras = [b for b in session.devices.values() if b.voice_only]
        self.assertEqual([b.cfg.name for b in extras], ["加湿器"])
        params = query_params(session)
        dids = {row["did"] for row in params}
        self.assertIn("111", dids)
        self.assertNotIn("222", dids)

    def test_query_skips_voice_only(self) -> None:
        cfg = DeviceConfig(id="x", name="X", type="switch", mijia_name="X")
        bound = BoundDevice(
            cfg=cfg,
            did="9",
            on_prop=None,
            voice_only=True,
        )
        from dock_hub.mijia_bridge import MijiaSession, PropRef

        bound.on_prop = PropRef(name="on", siid=2, piid=1, type="bool")
        session = MijiaSession(api=FakeApi([]), devices={"x": bound})
        self.assertEqual(query_params(session), [])

    def test_wire_companion_includes_voice_only(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "devices": [
                    {"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯"},
                ],
                "mini": False,
            }
        )
        hub = DockHub(cfg)
        on_prop = PropRef(name="on", siid=2, piid=1, type="bool")
        extra = DeviceConfig(id="m222", name="加湿器", type="switch", mijia_name="加湿器")
        hub.session = MijiaSession(
            api=FakeApi([]),
            devices={
                "lamp": BoundDevice(cfg=cfg.devices[0], did="111", on_prop=on_prop),
                "m222": BoundDevice(cfg=extra, did="222", on_prop=on_prop, voice_only=True),
            },
        )
        hub._wire_companion()
        by_name = {item["name"]: item["id"] for item in hub.companion._skills}
        self.assertEqual(by_name["台灯"], "lamp")
        self.assertEqual(by_name["加湿器"], "m222")
