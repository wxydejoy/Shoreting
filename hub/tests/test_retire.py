"""T7 退役回滚开关单测。默认全关 = 行为不变；翻 true → 对应路由组 404 retired。"""

from __future__ import annotations

import unittest

from dock_hub.retire import (
    RETIRE_GROUPS,
    parse_retire,
    retired_group_for,
)


class ParseRetireTests(unittest.TestCase):
    def test_empty_is_all_off(self):
        self.assertEqual(parse_retire(None), {})
        self.assertEqual(parse_retire({}), {})

    def test_known_group_flips(self):
        self.assertEqual(parse_retire({"mijia": True}), {"mijia": True})

    def test_unknown_group_rejected(self):
        with self.assertRaises(ValueError):
            parse_retire({"mijia_typo": True})

    def test_non_bool_rejected(self):
        with self.assertRaises(ValueError):
            parse_retire({"mijia": "yes"})

    def test_non_dict_rejected(self):
        with self.assertRaises(ValueError):
            parse_retire(["mijia"])

    def test_groups_documented(self):
        # 每个组的说明都要写清「谁是替代件」，防止开关失去语义
        for name, description in RETIRE_GROUPS.items():
            self.assertIn("——", description, f"retire 组 {name} 缺替代件说明")


class RouteGuardTests(unittest.TestCase):
    def test_no_flags_means_nothing_retired(self):
        for path in ("/v1/devices/lamp/command", "/v1/companion/chat", "/v1/music", "/v1/diary", "/metrics"):
            self.assertIsNone(retired_group_for(path, {}))

    def test_mijia_group_covers_its_routes(self):
        flags = {"mijia": True}
        self.assertEqual(retired_group_for("/v1/devices/lamp/command", flags), "mijia")
        self.assertEqual(retired_group_for("/v1/scenes/bedtime", flags), "mijia")
        self.assertEqual(retired_group_for("/v1/leave", flags), "mijia")
        self.assertIsNone(retired_group_for("/v1/companion/chat", flags))

    def test_prefix_boundary(self):
        # 前缀必须是路由边界：/v1/musicX 不能被 /v1/music 误伤
        flags = {"music": True}
        self.assertIsNone(retired_group_for("/v1/musicx", flags))

    def test_snapshot_and_health_never_retired(self):
        flags = {name: True for name in RETIRE_GROUPS}
        self.assertIsNone(retired_group_for("/v1/snapshot", flags))
        self.assertIsNone(retired_group_for("/health", flags))
        self.assertIsNone(retired_group_for("/v1/snapshot?since=1", flags))

    def test_monitoring_group(self):
        flags = {"monitoring": True}
        self.assertEqual(retired_group_for("/v1/telemetry", flags), "monitoring")
        self.assertEqual(retired_group_for("/v1/power", flags), "monitoring")
        self.assertEqual(retired_group_for("/metrics", flags), "monitoring")


if __name__ == "__main__":
    unittest.main()
