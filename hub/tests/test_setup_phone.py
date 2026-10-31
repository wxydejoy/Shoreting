"""手机端外观 / 电源 / 天气从控制台写回，并跟着快照下发。

这些值原本只存在手机的 DataStore 里，控制台改不到。现在 hub.yaml 存一份权威
副本，挂 /v1/snapshot 的 phone 段下发。这份测试盯五件事：

1. 没在控制台改过（rev == 0）时，yaml 不写 phone 段、快照也不带这个字段；
2. 只提交一部分字段时，没提交的沿用当前值，不回到默认；
3. 每次保存 rev 自增一次——手机靠它判断「要不要套用」；
4. 越界（透明度 101、圆角 4）和枚举写错（style: glass）整单拒绝，不静默截断；
5. 城市留空 = 保持原值，别把天气城市抹成空字符串。
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from dock_hub.config import HubConfig, phone_to_raw, hub_config_to_raw, parse_config
from dock_hub.service import DockHub
from dock_hub.setup_ui import _clean_phone, _payload_to_raw, apply_phone


def _cfg(path: Path | None = None, **extra) -> HubConfig:
    raw = {
        "name": "study",
        "token": "secret-token-value",
        "port": 17890,
        "devices": [{"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯"}],
        "guest": {"hours": 4},
        "schedules": [{"id": "morning", "at": "07:30", "on": ["lamp"]}],
    }
    raw.update(extra)
    return parse_config(raw, path)


class _StubHub:
    """apply_phone 只用到这三个成员：config、reload_config、note_change。"""

    def __init__(self, cfg: HubConfig) -> None:
        self.config = cfg
        self.reloaded = 0
        self.notes = 0

    def reload_config(self, cfg: HubConfig) -> None:
        self.reloaded += 1
        self.config = cfg

    def note_change(self) -> None:
        self.notes += 1


class PhoneDefaultsTest(unittest.TestCase):
    def test_defaults_match_android(self) -> None:
        phone = _cfg().phone
        self.assertEqual(phone.rev, 0)
        self.assertEqual(phone.tile.style, "ink")
        self.assertEqual(phone.tile.opacity, 42)
        self.assertEqual(phone.tile.corner, 20)
        self.assertEqual(phone.type.font, "system")
        self.assertEqual(phone.type.clock, 88)
        self.assertEqual(phone.type.stats, 20)
        self.assertEqual(phone.type.tile, 15)
        self.assertEqual(phone.type.chip, 15)
        self.assertTrue(phone.screen.keep_on)
        self.assertEqual(phone.screen.sleep_sec, 60)
        self.assertTrue(phone.weather.enabled)
        self.assertEqual(phone.weather.city, "上海")

    def test_rev_zero_not_written_to_yaml(self) -> None:
        raw = hub_config_to_raw(_cfg())
        self.assertNotIn("phone", raw)

    def test_snapshot_omits_phone_until_saved(self) -> None:
        cfg = _cfg()
        self.assertIsNone(DockHub._phone_payload(types.SimpleNamespace(config=cfg)))

    def test_snapshot_carries_phone_after_saved(self) -> None:
        cfg = _cfg(phone={"rev": 3, "weather": {"city": "杭州"}})
        payload = DockHub._phone_payload(types.SimpleNamespace(config=cfg))
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["rev"], 3)
        self.assertEqual(payload["weather"]["city"], "杭州")
        self.assertEqual(payload["tile"]["style"], "ink")


class PhoneParseTest(unittest.TestCase):
    def test_partial_section_keeps_other_values(self) -> None:
        cfg = _cfg(phone={"tile": {"opacity": 70}})
        self.assertEqual(cfg.phone.tile.opacity, 70)
        self.assertEqual(cfg.phone.tile.style, "ink")
        self.assertEqual(cfg.phone.tile.corner, 20)

    def test_round_trip(self) -> None:
        cfg = _cfg(phone={"rev": 2, "type": {"font": "inter", "clock": 120}})
        raw = hub_config_to_raw(cfg)["phone"]
        self.assertEqual(raw["rev"], 2)
        again = parse_config(hub_config_to_raw(cfg)).phone
        self.assertEqual(again.type.font, "inter")
        self.assertEqual(again.type.clock, 120)

    def test_out_of_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _cfg(phone={"tile": {"opacity": 101}})
        with self.assertRaises(ValueError):
            _cfg(phone={"tile": {"corner": 4}})
        with self.assertRaises(ValueError):
            _cfg(phone={"type": {"clock": 200}})
        with self.assertRaises(ValueError):
            _cfg(phone={"screen": {"sleep_sec": 5}})

    def test_bad_enum_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _cfg(phone={"tile": {"style": "glass"}})
        with self.assertRaises(ValueError):
            _cfg(phone={"type": {"font": "comic"}})

    def test_blank_city_keeps_previous(self) -> None:
        cfg = _cfg(phone={"weather": {"city": "杭州"}})
        self.assertEqual(cfg.phone.weather.city, "杭州")
        self.assertEqual(_cfg(phone={"weather": {"city": "  "}}).phone.weather.city, "上海")


class CleanPhoneTest(unittest.TestCase):
    def test_merge_only_touches_submitted_fields(self) -> None:
        cfg = _cfg(phone={"rev": 1, "tile": {"style": "frost", "corner": 26}})
        cleaned = _clean_phone({"weather": {"city": "苏州"}}, cfg)
        self.assertEqual(cleaned["tile"]["style"], "frost")
        self.assertEqual(cleaned["tile"]["corner"], 26)
        self.assertEqual(cleaned["weather"]["city"], "苏州")
        self.assertTrue(cleaned["weather"]["enabled"])

    def test_rev_increments_each_save(self) -> None:
        cfg = _cfg()
        first = _clean_phone({"tile": {"opacity": 60}}, cfg)
        self.assertEqual(first["rev"], 1)
        cfg2 = _cfg(phone=first)
        second = _clean_phone({"tile": {"opacity": 61}}, cfg2)
        self.assertEqual(second["rev"], 2)

    def test_empty_payload_is_a_noop(self) -> None:
        # 什么都没提交就别动 rev：外观和布局各管各的，空手不该盖空章
        cfg = _cfg(phone={"rev": 4})
        self.assertEqual(_clean_phone({}, cfg)["rev"], 4)


class PhoneLayoutTest(unittest.TestCase):
    """主屏布局：独立 rev、占位矩形、快照下发。"""

    def test_layout_round_trip_and_clamp(self) -> None:
        cfg = _cfg(phone={"layout": {"rev": 2, "modules": {
            "Clock": {"x": 0.2, "y": 0.3, "w": 0.5, "h": 0.4},
            "Media": {"x": 0, "y": 0, "w": 0.02, "h": 0.02, "visible": False},
        }}})
        self.assertEqual(cfg.phone.layout.rev, 2)
        clock = cfg.phone.layout.modules["Clock"]
        self.assertAlmostEqual(clock["w"], 0.5)
        media = cfg.phone.layout.modules["Media"]
        # w/h 太小 → 占位矩形：位置归零，显隐保留
        self.assertEqual(media["w"], 0.0)
        self.assertFalse(media["visible"])
        # 往返
        again = parse_config(hub_config_to_raw(cfg)).phone.layout
        self.assertEqual(again.rev, 2)
        self.assertIn("Clock", again.modules)
        # 没存过布局时 yaml 不带这一段
        self.assertNotIn("layout", hub_config_to_raw(_cfg()).get("phone", {}))

    def test_bad_layout_values_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _cfg(phone={"layout": {"modules": {"Clock": {"x": "abc"}}}})
        with self.assertRaises(ValueError):
            _cfg(phone={"layout": {"rev": -1}})
        with self.assertRaises(ValueError):
            _cfg(phone={"layout": {"modules": {"Clock": [0.1, 0.2, 0.3, 0.4]}}})

    def test_snapshot_carries_layout(self) -> None:
        cfg = _cfg(phone={"rev": 1, "layout": {"rev": 1, "modules": {
            "Clock": {"x": 0.1, "y": 0, "w": 0.3, "h": 0.2},
        }}})
        payload = DockHub._phone_payload(types.SimpleNamespace(config=cfg))
        assert payload is not None
        self.assertEqual(payload["layout"]["rev"], 1)
        self.assertIn("Clock", payload["layout"]["modules"])

    def test_layout_only_save_still_reaches_snapshot(self) -> None:
        # 外观从没改过、只存了布局：phone 段也必须下发，否则布局到不了手机
        cfg = _cfg(phone={"layout": {"rev": 1, "modules": {
            "Clock": {"x": 0.1, "y": 0, "w": 0.3, "h": 0.2},
        }}})
        self.assertEqual(cfg.phone.rev, 0)
        payload = DockHub._phone_payload(types.SimpleNamespace(config=cfg))
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["layout"]["rev"], 1)

    def test_layout_save_does_not_bump_look_rev(self) -> None:
        cfg = _cfg(phone={"rev": 5, "tile": {"opacity": 70}, "layout": {"rev": 1, "modules": {}}})
        cleaned = _clean_phone(
            {"layout": {"modules": {"Clock": {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.3}}}}, cfg
        )
        self.assertEqual(cleaned["rev"], 5)  # 外观的 rev 没动
        self.assertEqual(cleaned["layout"]["rev"], 2)
        self.assertEqual(cleaned["tile"]["opacity"], 70)
        # 反过来：只存外观时布局也不动
        cleaned2 = _clean_phone({"tile": {"opacity": 71}}, cfg)
        self.assertEqual(cleaned2["rev"], 6)
        self.assertEqual(cleaned2["layout"]["rev"], 1)

    def test_apply_layout_only_writes_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            hub = _StubHub(_cfg(path))
            result = apply_phone(hub, {"layout": {"modules": {
                "Clock": {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.3},
            }}})
            self.assertTrue(result["ok"])
            self.assertEqual(hub.config.phone.rev, 0)
            self.assertEqual(hub.config.phone.layout.rev, 1)
            self.assertIn("布局第 1 版", result["message"])
            loaded = parse_config(_load(path), path)
            self.assertEqual(loaded.phone.layout.rev, 1)
            self.assertAlmostEqual(loaded.phone.layout.modules["Clock"]["x"], 0.3)
            self.assertEqual(len(loaded.devices), 1)  # 其它段落还在


class ApplyPhoneTest(unittest.TestCase):
    def test_writes_yaml_and_wakes_long_poll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            cfg = _cfg(path)
            hub = _StubHub(cfg)
            result = apply_phone(hub, {"tile": {"style": "solid", "opacity": 80}})
            self.assertTrue(result["ok"])
            self.assertEqual(hub.notes, 1)
            self.assertEqual(hub.reloaded, 1)
            self.assertEqual(result["phone"]["rev"], 1)
            self.assertEqual(result["phone"]["tile"]["style"], "solid")
            # 落盘的是可重新解析的 yaml，且其它段落还在
            loaded = parse_config(_load(path), path)
            self.assertEqual(loaded.phone.tile.style, "solid")
            self.assertEqual(loaded.phone.tile.opacity, 80)
            self.assertEqual(len(loaded.devices), 1)
            self.assertTrue(loaded.guest.enabled)
            self.assertEqual(len(loaded.schedules), 1)

    def test_second_save_bumps_rev_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            hub = _StubHub(_cfg(path))
            apply_phone(hub, {"weather": {"city": "杭州"}})
            result = apply_phone(hub, {"weather": {"city": "苏州"}})
            self.assertEqual(result["phone"]["rev"], 2)
            self.assertEqual(hub.config.phone.weather.city, "苏州")

    def test_bad_value_rejects_whole_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.yaml"
            hub = _StubHub(_cfg(path))
            with self.assertRaises(ValueError):
                apply_phone(hub, {"tile": {"opacity": 900}})
            self.assertFalse(path.exists())  # 没有写出半截配置

    def test_payload_round_trips_through_generic_save(self) -> None:
        cfg = _cfg()
        raw = _payload_to_raw({"phone": {"screen": {"keep_on": False}}}, cfg)
        self.assertEqual(raw["phone"]["rev"], 1)
        self.assertFalse(raw["phone"]["screen"]["keep_on"])
        # 通用保存也不能把没提交的段落洗掉
        self.assertEqual(len(raw["devices"]), 1)
        self.assertTrue(raw["guest"])


class PhonePayloadShapeTest(unittest.TestCase):
    def test_payload_field_names_match_android(self) -> None:
        cfg = _cfg(phone={"rev": 1})
        payload = phone_to_raw(cfg.phone)
        self.assertEqual(set(payload), {"rev", "tile", "type", "screen", "weather"})
        self.assertEqual(set(payload["type"]), {"font", "clock", "stats", "tile", "chip"})
        self.assertEqual(set(payload["screen"]), {"keep_on", "sleep_sec"})
        self.assertEqual(set(payload["weather"]), {"enabled", "city"})


def _load(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
