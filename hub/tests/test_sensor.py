from __future__ import annotations

import unittest
from unittest.mock import patch

from dock_hub.config import parse_config
from dock_hub.mijia_bridge import bind_from_config, query_params
from dock_hub.service import DockHub


def _cfg(devices, **extra):
    raw = {
        "name": "study",
        "token": "secret-token-value",
        "devices": devices,
        "mini": False,
        **extra,
    }
    return parse_config(raw)


class SensorConfigTest(unittest.TestCase):
    def test_parses_sensor_device(self) -> None:
        cfg = _cfg(
            [
                {
                    "id": "desk",
                    "name": "书桌",
                    "type": "sensor",
                    "mijia_name": "温湿度计",
                }
            ]
        )
        desk = cfg.devices[0]
        self.assertTrue(desk.is_sensor)
        self.assertTrue(desk.is_mijia)
        self.assertEqual([item.prop for item in desk.props], ["temperature", "humidity"])
        self.assertFalse(desk.props[0].write)

    def test_roundtrip_keeps_sensor_props(self) -> None:
        from dock_hub.config import hub_config_to_raw

        cfg = _cfg(
            [
                {
                    "id": "desk",
                    "name": "书桌",
                    "type": "sensor",
                    "mijia_name": "温湿度计",
                    "props": [
                        {"id": "celsius", "prop": "temperature", "write": False, "unit": "°C"},
                        {"id": "humidity", "prop": "relative-humidity", "write": False, "unit": "%"},
                    ],
                }
            ]
        )
        raw = hub_config_to_raw(cfg)["devices"][0]
        self.assertEqual(raw["type"], "sensor")
        self.assertEqual(len(raw["props"]), 2)
        again = parse_config({**hub_config_to_raw(cfg), "token": "secret-token-value", "mini": False})
        self.assertEqual(again.devices[0].props[1].prop, "relative-humidity")


class SensorSnapshotTest(unittest.TestCase):
    def test_offline_sensor_still_in_devices(self) -> None:
        hub = DockHub(
            _cfg(
                [
                    {"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯"},
                    {"id": "desk", "name": "书桌", "type": "sensor", "mijia_name": "温湿度计"},
                    {
                        "id": "cursor",
                        "name": "Cursor",
                        "type": "action",
                        "path": "/tmp/cursor.exe",
                    },
                ]
            )
        )
        snap = hub.snapshot()
        ids = [item["id"] for item in snap["devices"]]
        self.assertEqual(ids, ["lamp", "desk", "cursor"])
        desk = next(item for item in snap["devices"] if item["id"] == "desk")
        self.assertEqual(desk["type"], "sensor")
        self.assertFalse(desk["online"])

    def test_temperature_block_becomes_sensor_tile(self) -> None:
        hub = DockHub(
            _cfg(
                [{"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯"}],
                temperature={
                    "id": "desk",
                    "name": "书桌",
                    "mijia_name": "温湿度计",
                    "celsius_prop": "temperature",
                    "humidity_prop": "humidity",
                },
            )
        )
        snap = hub.snapshot()
        ids = [item["id"] for item in snap["devices"]]
        self.assertIn("desk", ids)
        desk = next(item for item in snap["devices"] if item["id"] == "desk")
        self.assertEqual(desk["type"], "sensor")


class SensorBindTest(unittest.TestCase):
    def test_bind_sensor_without_on_prop(self) -> None:
        from dock_hub.config import DeviceConfig

        class FakeApi:
            def get_devices_list(self):
                return [{"name": "温湿度计", "did": "333", "model": "sensor"}]

        def info(model, cache_path=None):
            return {
                "properties": [
                    {"name": "temperature", "type": "float", "method": {"siid": 3, "piid": 1}},
                    {"name": "relative-humidity", "type": "float", "method": {"siid": 3, "piid": 2}},
                ]
            }

        cfg = DeviceConfig(
            id="desk",
            name="书桌",
            type="sensor",
            mijia_name="温湿度计",
        )
        with patch("dock_hub.mijia_bridge.get_device_info", side_effect=info):
            session = bind_from_config(FakeApi(), [cfg], None)
        bound = session.devices["desk"]
        self.assertIsNone(bound.on_prop)
        self.assertIn("celsius", bound.extra_props)
        params = query_params(session)
        self.assertEqual({row["did"] for row in params}, {"333"})


class ExtraPropCommandTest(unittest.TestCase):
    def test_command_writes_extra_prop(self) -> None:
        from dock_hub.mijia_bridge import BoundDevice, MijiaSession, PropRef

        cfg = _cfg(
            [
                {
                    "id": "ac",
                    "name": "空调",
                    "type": "switch",
                    "mijia_name": "空调",
                    "props": [{"id": "mode", "prop": "mode", "name": "模式", "write": True}],
                }
            ]
        )
        writes: list[dict] = []

        class FakeApi:
            available = True

            def set_devices_prop(self, payload):
                writes.extend(payload)
                return [{"did": "1", "siid": 2, "piid": 1, "code": 0}]

            def get_devices_prop(self, params):
                return [{"did": "1", "siid": 2, "piid": 1, "code": 0, "value": True}]

        hub = DockHub(cfg)
        hub.session = MijiaSession(
            api=FakeApi(),
            devices={
                "ac": BoundDevice(
                    cfg=cfg.devices[0],
                    did="1",
                    on_prop=PropRef("on", 2, 1, "bool"),
                    extra_props={"mode": PropRef("mode", 3, 1, "uint")},
                )
            },
        )
        result = hub.command("ac", {"props": {"mode": 2}})
        self.assertTrue(any(row["siid"] == 3 and row["value"] == 2 for row in writes))
        extra = {item["id"]: item for item in result.get("props") or []}
        self.assertEqual(extra["mode"]["number"], 2.0)

    def test_command_writes_ac_target_temperature(self) -> None:
        from dock_hub.mijia_bridge import BoundDevice, MijiaSession, PropRef

        cfg = _cfg(
            [
                {
                    "id": "ac",
                    "name": "空调",
                    "type": "switch",
                    "mijia_name": "空调",
                    "props": [
                        {
                            "id": "temp",
                            "prop": "target-temperature",
                            "name": "设定温度",
                            "write": True,
                            "unit": "°C",
                            "min": 16,
                            "max": 30,
                        }
                    ],
                }
            ]
        )
        writes: list[dict] = []

        class FakeApi:
            available = True

            def set_devices_prop(self, payload):
                writes.extend(payload)
                return [{"did": "1", "siid": 3, "piid": 4, "code": 0}]

            def get_devices_prop(self, params):
                return [{"did": "1", "siid": 2, "piid": 1, "code": 0, "value": True}]

        hub = DockHub(cfg)
        hub.session = MijiaSession(
            api=FakeApi(),
            devices={
                "ac": BoundDevice(
                    cfg=cfg.devices[0],
                    did="1",
                    on_prop=PropRef("on-3", 2, 1, "bool"),
                    extra_props={"temp": PropRef("target-temperature", 3, 4, "uint")},
                )
            },
        )
        result = hub.command("ac", {"on": True, "props": {"temp": 24}})
        self.assertTrue(any(row["siid"] == 3 and row["piid"] == 4 and row["value"] == 24 for row in writes))
        extra = {item["id"]: item for item in result.get("props") or []}
        self.assertEqual(extra["temp"]["number"], 24.0)
