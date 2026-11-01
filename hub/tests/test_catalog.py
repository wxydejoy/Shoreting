"""全量米家设备能力目录：构建、prompt、解析、值域钳制、执行。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dock_hub.actions import apply_device_skills
from dock_hub.errors import HubError
from dock_hub.intents import (
    looks_like_control,
    parse_do_actions,
    parse_set_actions,
)
from dock_hub.mijia_catalog import (
    CatalogEntry,
    CatalogProp,
    build_catalog,
    catalog_prompt,
    coerce_catalog_value,
    entries_mentioned,
    read_plan,
    reading_lines,
)


def _prop(name: str, typ: str = "bool", siid: int = 2, piid: int = 1, **extra: object) -> dict:
    base = {"name": name, "type": typ, "method": {"siid": siid, "piid": piid}}
    for key, value in extra.items():
        # spec 里的枚举字段叫 value-list（带横线），Python 关键字写不出，转换一下
        base[key.replace("_", "-")] = value
    return base


class _FakeApi:
    """ mimicking mijiaAPI：设备列表 + spec 缓存。 """

    def __init__(self, devices: list[dict], specs: dict[str, dict]) -> None:
        self._devices = devices
        self._specs = specs

    def get_devices_list(self) -> list[dict]:
        return self._devices

    def get_devices_prop(self, params: list[dict]) -> list[dict]:
        rows = []
        for param in params:
            rows.append({"did": param["did"], "siid": param["siid"], "piid": param["piid"], "code": 0, "value": 0})
        return rows


class _FakeSpec:
    """顶替 get_device_info。"""

    def __init__(self, specs: dict[str, dict]) -> None:
        self._specs = specs

    def __call__(self, model: str, cache_path: object = None) -> dict:
        return self._specs.get(model, {})


AC_SPEC = {
    "properties": [
        _prop("on-3", siid=3, piid=1),
        _prop(
            "target-temperature",
            "float",
            siid=3,
            piid=4,
            rw="rw",
            range=[16.0, 30.0, 0.5],
        ),
        _prop(
            "mode",
            "uint",
            siid=3,
            piid=2,
            rw="rw",
            value_list=[
                {"value": 0, "description": "制冷"},
                {"value": 1, "description": "制热"},
                {"value": 2, "description": "自动"},
            ],
        ),
        _prop("temperature", "float", siid=3, piid=7, rw="r"),
    ],
    "actions": [
        {"name": "start-sweep", "description": "开始清扫", "method": {"siid": 4, "aiid": 1}}
    ],
}

SWEEPER_SPEC = {
    "properties": [_prop("battery-level", "uint", siid=3, piid=1, rw="r", range=[0, 100, 1])],
    "actions": [
        {"name": "start-sweep", "description": "开始清扫", "method": {"siid": 2, "aiid": 1}},
        {"name": "stop-sweeping", "description": "停止清扫", "method": {"siid": 2, "aiid": 2}},
    ],
}


class BuildCatalogTest(unittest.TestCase):
    def test_all_devices_enter_catalog_with_ids(self) -> None:
        api = _FakeApi(
            devices=[
                {"name": "空调伴侣", "model": "acpartner", "did": "111"},
                {"name": "扫地机器人", "model": "sweeper", "did": "222"},
            ],
            specs={"acpartner": AC_SPEC, "sweeper": SWEEPER_SPEC},
        )
        with patch("dock_hub.mijia_catalog._spec_entries", _FakeSpec({"acpartner": AC_SPEC, "sweeper": SWEEPER_SPEC})):
            entries = build_catalog(api)
        self.assertEqual([e.name for e in entries], ["空调伴侣", "扫地机器人"])
        self.assertEqual([e.id for e in entries], ["m111", "m222"])
        ac = entries[0]
        temp = ac.find_prop("target_temperature")
        self.assertIsNotNone(temp)
        assert temp is not None
        self.assertTrue(temp.writable)
        self.assertEqual(temp.value_min, 16.0)
        self.assertEqual(temp.value_max, 30.0)
        mode = ac.find_prop("mode")
        assert mode is not None
        self.assertEqual(mode.value_list, [("0", "制冷"), ("1", "制热"), ("2", "自动")])
        sweeper = entries[1]
        self.assertIsNotNone(sweeper.find_action("start-sweep"))

    def test_bound_device_keeps_config_id(self) -> None:
        api = _FakeApi(devices=[{"name": "台灯", "model": "lamp", "did": "333"}], specs={})
        with patch("dock_hub.mijia_catalog._spec_entries", _FakeSpec({"lamp": AC_SPEC})):
            entries = build_catalog(api, name_to_id={"台灯": "lamp"})
        self.assertEqual(entries[0].id, "lamp")


class CatalogPromptTest(unittest.TestCase):
    def _entry(self) -> CatalogEntry:
        return CatalogEntry(
            id="m-111",
            name="空调",
            model="acpartner",
            did="111",
            props=[
                CatalogProp(name="on-3", siid=3, piid=1, type="bool", desc="开关", writable=True, readable=True),
                CatalogProp(
                    name="target-temperature",
                    siid=3,
                    piid=4,
                    type="float",
                    desc="目标温度",
                    writable=True,
                    readable=True,
                    value_min=16,
                    value_max=30,
                ),
            ],
            actions=[],
        )

    def test_prompt_lists_device_and_syntax(self) -> None:
        text = catalog_prompt([self._entry()])
        self.assertIn("空调（m-111）", text)
        self.assertIn("目标温度（target-temperature） 16–30", text)
        self.assertIn("ACTION: <id>.set.<属性名>.<值>", text)
        self.assertIn("ACTION: <id>.do.<动作名>", text)

    def test_empty_catalog_empty_prompt(self) -> None:
        self.assertEqual(catalog_prompt([]), "")
        bare = CatalogEntry(id="m-1", name="网关", model="gw", did="1")
        self.assertEqual(catalog_prompt([bare]), "")


class MentionsTest(unittest.TestCase):
    def test_name_mentioned(self) -> None:
        ac = CatalogEntry(id="m-111", name="空调伴侣", model="m", did="111")
        self.assertEqual(entries_mentioned([ac], "空调伴侣多少度"), [ac])
        self.assertEqual(entries_mentioned([ac], "灯开着吗"), [])

    def test_cap_read_devices(self) -> None:
        entries = [
            CatalogEntry(
                id=f"m-{i}",
                name=f"设备{i}",
                model="m",
                did=str(i),
                props=[CatalogProp(name="on", siid=2, piid=1, type="bool", desc="开关", writable=True, readable=True)],
            )
            for i in range(9)
        ]
        self.assertEqual(len(entries_mentioned(entries, " ".join(e.name for e in entries))), 9)
        plan = read_plan(entries)
        self.assertEqual(len(plan), 4)


class ReadingLinesTest(unittest.TestCase):
    def test_bool_and_values(self) -> None:
        ac = CatalogEntry(
            id="m-111",
            name="空调",
            model="m",
            did="111",
            props=[
                CatalogProp(name="on-3", siid=3, piid=1, type="bool", desc="开关", writable=True, readable=True),
                CatalogProp(name="target-temperature", siid=3, piid=4, type="float", desc="目标温度", writable=True, readable=True),
            ],
        )
        rows = [
            {"did": "111", "siid": 3, "piid": 1, "code": 0, "value": True},
            {"did": "111", "siid": 3, "piid": 4, "code": 0, "value": 26.0},
            {"did": "111", "siid": 3, "piid": 9, "code": -1, "value": None},
        ]
        self.assertEqual(reading_lines(ac, rows), "空调：开关 开，目标温度 26")


class CoerceTest(unittest.TestCase):
    def _prop(self, **kw: object) -> CatalogProp:
        return CatalogProp(
            name="target-temperature",
            siid=3,
            piid=4,
            type="float",
            desc="目标温度",
            writable=True,
            readable=True,
            value_min=16,
            value_max=30,
            **kw,
        )

    def test_clamp_into_range(self) -> None:
        self.assertEqual(coerce_catalog_value(self._prop(), "40"), 30.0)
        self.assertEqual(coerce_catalog_value(self._prop(), "10"), 16.0)
        self.assertEqual(coerce_catalog_value(self._prop(), "26.5"), 26.5)

    def test_bool_parse(self) -> None:
        p = CatalogProp(name="on", siid=2, piid=1, type="bool", desc="开关", writable=True, readable=True)
        self.assertIs(coerce_catalog_value(p, "true"), True)
        self.assertIs(coerce_catalog_value(p, "0"), False)
        with self.assertRaises(ValueError):
            coerce_catalog_value(p, "26")


class ParseTest(unittest.TestCase):
    def test_set_and_do(self) -> None:
        raw = "好，这就调。\nACTION: m-111.set.target-temperature.26\nACTION: m-222.do.start-sweep\n"
        self.assertEqual(parse_set_actions(raw), [("m-111", "target-temperature", "26")])
        self.assertEqual(parse_do_actions(raw), [("m-222", "start-sweep")])

    def test_underscore_alias(self) -> None:
        raw = "ACTION: air.set.target_temperature.22"
        self.assertEqual(parse_set_actions(raw), [("air", "target_temperature", "22")])

    def test_control_gate_accepts_device_words(self) -> None:
        self.assertTrue(looks_like_control("空调二十六度"))
        self.assertTrue(looks_like_control("扫地机器人开始清扫"))
        self.assertFalse(looks_like_control("今天好累啊"))


class ApplyCatalogTest(unittest.TestCase):
    def test_set_via_world_seam(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def device_prop(ident: str, prop: str, value: str) -> dict:
            calls.append((ident, prop, value))
            return {"id": "m-111", "name": "空调", "value": 26.0}

        events = []
        applied, events = apply_device_skills(
            user_text="空调调到二十六度",
            raw_reply="好。\nACTION: m-111.set.target-temperature.26",
            turn="t1",
            skills=[],
            aliases={},
            run_skill=None,
            world={"device_prop": device_prop},
            note=lambda *a, **k: None,
        )
        self.assertTrue(applied)
        self.assertEqual(calls, [("m-111", "target-temperature", "26")])
        self.assertTrue(events[0]["ok"])

    def test_chatty_reply_not_executed(self) -> None:
        applied, _events = apply_device_skills(
            user_text="今天好累啊",
            raw_reply="歇着吧。\nACTION: m-111.set.target-temperature.26",
            turn="t1",
            skills=[],
            aliases={},
            run_skill=None,
            world={"device_prop": lambda *a: {"id": "x"}},
            note=lambda *a, **k: None,
        )
        self.assertFalse(applied)

    def test_failure_event(self) -> None:
        def device_prop(ident: str, prop: str, value: str) -> dict:
            raise HubError("offline", "空调离线")

        applied, events = apply_device_skills(
            user_text="把空调关小点",
            raw_reply="好。\nACTION: m-111.set.fan-level.1",
            turn="t1",
            skills=[],
            aliases={},
            run_skill=None,
            world={"device_prop": device_prop},
            note=lambda *a, **k: None,
        )
        self.assertFalse(applied)
        self.assertEqual(events[0]["code"], "offline")
        self.assertFalse(events[0]["ok"])


if __name__ == "__main__":
    unittest.main()
