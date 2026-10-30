"""一句话壁纸（F39）：一天一句、相邻不重样、句式守规矩、关掉就没了。"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.wallpaper import LINES, wallpaper_line


class WallpaperLineTest(unittest.TestCase):
    def test_stable_within_day_and_differs_next_day(self) -> None:
        today = date(2026, 10, 31)
        self.assertEqual(wallpaper_line(today), wallpaper_line(today))
        self.assertNotEqual(wallpaper_line(today), wallpaper_line(date(2026, 11, 1)))

    def test_lines_follow_the_house_rules(self) -> None:
        for line in LINES:
            self.assertLessEqual(len(line), 20, line)
            self.assertNotIn("！", line)
            self.assertNotIn("!", line)

    def test_pool_is_worth_rotating(self) -> None:
        self.assertGreaterEqual(len(LINES), 28)


class WallpaperConfigTest(unittest.TestCase):
    def _parse(self, raw: dict):
        return parse_config(
            {"name": "study", "token": "secret-token-value", "devices": [], **raw}
        )

    def test_on_by_default(self) -> None:
        self.assertTrue(self._parse({}).wallpaper.enabled)
        self.assertTrue(self._parse({"wallpaper": True}).wallpaper.enabled)

    def test_false_turns_off_and_roundtrips(self) -> None:
        cfg = self._parse({"wallpaper": False})
        self.assertFalse(cfg.wallpaper.enabled)
        raw = hub_config_to_raw(cfg)
        self.assertEqual(raw["wallpaper"], False)
        again = parse_config(raw)
        self.assertFalse(again.wallpaper.enabled)

    def test_enabled_drops_from_raw(self) -> None:
        self.assertNotIn("wallpaper", hub_config_to_raw(self._parse({})))

    def test_bad_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._parse({"wallpaper": "今天也要加油"})


if __name__ == "__main__":
    unittest.main()
