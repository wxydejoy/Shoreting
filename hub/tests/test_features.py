from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dock_hub.config import parse_config, save_config_raw
from dock_hub.features import (
    BY_KEY,
    FEATURES,
    GROUPS,
    apply_feature,
    coerce,
    describe,
    patch_text,
    remove_path,
)

RAW = {
    "name": "study",
    "token": "secret-token-value",
    "port": 17890,
    # 手写的注释：改一个开关不该把它洗掉
    "devices": [
        {"id": "desk-led", "name": "灯带", "type": "ws2812", "base_url": "http://192.168.14.33"},
        {"id": "lamp", "name": "台灯", "type": "light", "mijia_name": "台灯"},
        {"id": "steam", "name": "Steam", "type": "action", "run": {"program": "steam.exe"}},
    ],
    "scenes": [{"id": "bedtime", "name": "睡前"}],
    "proactive": {"enabled": True, "morning": {"at": "07:20"}},
    "calendar": {"url": "https://example.com/x.ics"},
}


class StubHub:
    """够 apply_feature 用的最小 hub：配置 + 热加载。"""

    def __init__(self, config):
        self.config = config
        self.reloads = 0

    def reload_config(self, config) -> None:
        self.config = config
        self.reloads += 1


class FakeHandler:
    def __init__(self) -> None:
        self.status = None
        self.payload = None

    def _json(self, status: int, payload: dict) -> None:
        self.status = status
        self.payload = payload


def write_config(tmp: str) -> Path:
    path = Path(tmp) / "hub.yaml"
    save_config_raw(dict(RAW), path)
    # safe_dump 会把注释写没，这里补一行，专门验「改开关不洗注释」
    text = path.read_text(encoding="utf-8")
    path.write_text("# 岸亭的配置\n" + text, encoding="utf-8")
    return path


class PatchTextTest(unittest.TestCase):
    def test_keeps_comments_and_other_sections(self) -> None:
        text = "# 顶部的注释\nproactive:\n  enabled: true\n\n# 中间\nscenes:\n- id: bedtime\n  name: 睡前\n"
        out = patch_text(text, ("proactive", "daily_limit"), 8)
        self.assertIn("# 顶部的注释", out)
        self.assertIn("  daily_limit: 8", out)
        self.assertIn("- id: bedtime", out)
        self.assertLess(out.index("daily_limit"), out.index("scenes:"))

    def test_nested_leaf_is_replaced_in_place(self) -> None:
        text = 'proactive:\n  morning:\n    at: "07:20"\n  enabled: true\n'
        out = patch_text(text, ("proactive", "morning", "at"), "06:50")
        self.assertIn('    at: "06:50"', out)
        self.assertIn("  enabled: true", out)

    def test_missing_section_is_created(self) -> None:
        text = "name: study\ntoken: t\n"
        out = patch_text(text, ("alarm", "at"), "07:00")
        self.assertTrue(out.startswith("name: study\ntoken: t\nalarm:\n  at: \"07:00\""))

    def test_scalar_section_expands_to_a_block(self) -> None:
        text = "name: study\nguest: false\ndiary: true\n"
        out = patch_text(text, ("guest", "hours"), 4)
        self.assertIn("guest:\n  hours: 4", out)
        self.assertIn("diary: true", out)

    def test_section_goes_to_false_when_disabled(self) -> None:
        text = "diary:\n  at: \"21:30\"\n"
        out = patch_text(text, ("diary",), False)
        self.assertEqual(out.strip(), "diary: false")

    def test_inline_flow_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            patch_text("alarm: {at: \"07:00\"}\n", ("alarm", "brightness"), 30)

    def test_remove_top_level_block(self) -> None:
        text = "calendar:\n  url: \"https://x\"\n  name: \"我的\"\nport: 1\n"
        out = remove_path(text, ("calendar",))
        self.assertEqual(out, "port: 1\n")

    def test_strings_are_quoted(self) -> None:
        out = patch_text("proactive:\n  enabled: true\n", ("proactive", "quiet"), ["23:00", "07:00"])
        self.assertIn('quiet: ["23:00", "07:00"]', out)


class CoerceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(dict(RAW))

    def test_bool_accepts_wire_shapes(self) -> None:
        feature = BY_KEY["wallpaper.enabled"]
        self.assertTrue(coerce(feature, True, self.cfg))
        self.assertTrue(coerce(feature, "true", self.cfg))
        self.assertFalse(coerce(feature, "off", self.cfg))
        with self.assertRaises(ValueError):
            coerce(feature, "也许", self.cfg)

    def test_range_is_checked(self) -> None:
        feature = BY_KEY["proactive.daily_limit"]
        self.assertEqual(coerce(feature, "8", self.cfg), 8)
        with self.assertRaises(ValueError) as caught:
            coerce(feature, 99, self.cfg)
        self.assertIn("每天最多说几句", str(caught.exception))

    def test_time_is_normalised(self) -> None:
        feature = BY_KEY["alarm.at"]
        self.assertEqual(coerce(feature, "7:30", self.cfg), "07:30")
        self.assertEqual(coerce(feature, "", self.cfg), "")
        with self.assertRaises(ValueError):
            coerce(feature, "25:00", self.cfg)

    def test_time_that_cannot_be_blank(self) -> None:
        # 日记、日落「推到几点」没有「留空 = 关着」的语义，空着要报错而不是静默关掉
        with self.assertRaises(ValueError) as caught:
            coerce(BY_KEY["diary.at"], "", self.cfg)
        self.assertIn("07:30", str(caught.exception))

    def test_device_must_exist(self) -> None:
        feature = BY_KEY["dusk.device"]
        self.assertEqual(coerce(feature, "desk-led", self.cfg), "desk-led")
        self.assertEqual(coerce(feature, "", self.cfg), "")
        with self.assertRaises(ValueError) as caught:
            coerce(feature, "nope", self.cfg)
        self.assertIn("没有 nope", str(caught.exception))

    def test_scene_must_exist(self) -> None:
        feature = BY_KEY["focus.on"]
        self.assertEqual(coerce(feature, "bedtime", self.cfg), "bedtime")
        with self.assertRaises(ValueError):
            coerce(feature, "开黑", self.cfg)

    def test_quiet_needs_two_clock_points(self) -> None:
        feature = BY_KEY["proactive.quiet"]
        self.assertEqual(coerce(feature, ["22:00", "7:00"], self.cfg), ["22:00", "07:00"])
        with self.assertRaises(ValueError):
            coerce(feature, ["22:00"], self.cfg)


class DescribeTest(unittest.TestCase):
    def test_catalog_has_groups_and_choices(self) -> None:
        cfg = parse_config(dict(RAW))
        data = describe(cfg)
        self.assertTrue(data["groups"])
        keys = {item["key"] for item in data["features"]}
        self.assertIn("proactive.enabled", keys)
        self.assertIn("dusk.device", keys)
        device_feature = next(item for item in data["features"] if item["key"] == "dusk.device")
        values = [option["value"] for option in device_feature["options"]]
        self.assertEqual(values, ["", "desk-led", "lamp"])
        scene_feature = next(item for item in data["features"] if item["key"] == "focus.on")
        self.assertEqual([o["value"] for o in scene_feature["options"]], ["", "bedtime"])

    def test_every_feature_points_at_a_real_group_and_path(self) -> None:
        groups = {group.id for group in GROUPS}
        keys = [feature.key for feature in FEATURES]
        self.assertEqual(len(keys), len(set(keys)), "开关的 key 不能重")
        for feature in FEATURES:
            self.assertIn(feature.group, groups, feature.key)
            self.assertTrue(feature.path, feature.key)
            self.assertTrue(feature.label, feature.key)
            if feature.kind in {"int", "number"}:
                self.assertIsNotNone(feature.low, feature.key)
                self.assertIsNotNone(feature.high, feature.key)


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_config(self.tmp.name)
        self.hub = StubHub(parse_config(dict(RAW), self.path))

    def test_sets_a_number_and_reloads(self) -> None:
        result = apply_feature(self.hub, "proactive.daily_limit", 8)
        self.assertTrue(result["ok"])
        self.assertEqual(self.hub.config.proactive.daily_limit, 8)
        self.assertEqual(self.hub.reloads, 1)
        self.assertIn("daily_limit: 8", self.path.read_text(encoding="utf-8"))
        self.assertTrue(self.path.read_text(encoding="utf-8").startswith("# 岸亭的配置"))

    def test_creates_missing_section(self) -> None:
        apply_feature(self.hub, "alarm.at", "07:00")
        self.assertEqual(self.hub.config.alarm.at, "07:00")
        self.assertIn("alarm:", self.path.read_text(encoding="utf-8"))

    def test_turns_things_off_with_a_scalar(self) -> None:
        apply_feature(self.hub, "diary.enabled", False)
        self.assertFalse(self.hub.config.diary.enabled)
        self.assertIn("diary: false", self.path.read_text(encoding="utf-8"))

    def test_clearing_calendar_drops_the_block(self) -> None:
        apply_feature(self.hub, "calendar.url", "")
        self.assertIsNone(self.hub.config.calendar)
        self.assertNotIn("calendar", self.path.read_text(encoding="utf-8"))

    def test_bad_value_leaves_the_file_alone(self) -> None:
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            apply_feature(self.hub, "alarm.at", "07:99")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.hub.reloads, 0)

    def test_unknown_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            apply_feature(self.hub, "proactive.nope", True)

    def test_whole_numbers_have_no_trailing_dot_zero(self) -> None:
        # guest.hours 是浮点字段，但人不写 6.0
        apply_feature(self.hub, "guest.hours", 6)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("hours: 6\n", text)
        self.assertNotIn("6.0", text)
        self.assertEqual(self.hub.config.guest.hours, 6)

    def test_fractional_values_keep_their_decimals(self) -> None:
        apply_feature(self.hub, "guest.hours", 2.5)
        self.assertIn("hours: 2.5", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.hub.config.guest.hours, 2.5)


class EndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_config(self.tmp.name)
        self.hub = StubHub(parse_config(dict(RAW), self.path))

    def test_get_returns_the_catalog(self) -> None:
        from dock_hub.setup_ui import handle_setup_get

        handler = FakeHandler()
        self.assertTrue(handle_setup_get(self.hub, "/setup/api/features", handler))
        self.assertEqual(handler.status, 200)
        self.assertTrue(handler.payload["ok"])
        self.assertTrue(handler.payload["features"])

    def test_post_writes_and_reports(self) -> None:
        from dock_hub.setup_ui import handle_setup_post

        handler = FakeHandler()
        handled = handle_setup_post(
            self.hub,
            "/setup/api/features",
            {"key": "proactive.enabled", "value": False},
            handler,
        )
        self.assertTrue(handled)
        self.assertEqual(handler.status, 200)
        self.assertFalse(self.hub.config.proactive.enabled)

    def test_post_rejects_bad_value(self) -> None:
        from dock_hub.setup_ui import handle_setup_post

        handler = FakeHandler()
        handle_setup_post(
            self.hub,
            "/setup/api/features",
            {"key": "limits.chat_per_min", "value": -5},
            handler,
        )
        self.assertEqual(handler.status, 400)
        self.assertFalse(handler.payload["ok"])


if __name__ == "__main__":
    unittest.main()
