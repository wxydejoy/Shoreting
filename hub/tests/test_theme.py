"""主题皮肤（F36）：日子算得对、配置查得严、生日一年就开口一次。"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.theme import OCCASION_ORDER, ThemeConfig, occasions_for


def _cfg(**theme):
    raw = {
        "name": "study",
        "token": "secret-token-value",
        "devices": [],
        **({"theme": theme} if theme else {}),
    }
    return parse_config(raw)


class OccasionsTest(unittest.TestCase):
    def test_spring_festival_including_new_years_eve(self) -> None:
        # 2026 春节是 2 月 17 日
        self.assertEqual(occasions_for(date(2026, 2, 17), ThemeConfig()), ["spring_festival"])
        self.assertEqual(occasions_for(date(2026, 2, 16), ThemeConfig()), ["spring_festival"])
        self.assertEqual(occasions_for(date(2026, 2, 15), ThemeConfig()), [])
        # 表之外的年份不硬猜
        self.assertEqual(occasions_for(date(2035, 2, 17), ThemeConfig()), [])

    def test_fixed_days(self) -> None:
        self.assertEqual(occasions_for(date(2026, 1, 1), ThemeConfig()), ["new_year"])
        self.assertEqual(occasions_for(date(2026, 12, 25), ThemeConfig()), ["christmas"])
        self.assertEqual(occasions_for(date(2026, 12, 24), ThemeConfig()), ["christmas"])
        self.assertEqual(occasions_for(date(2026, 12, 27), ThemeConfig()), [])

    def test_birthday_beats_festival(self) -> None:
        cfg = ThemeConfig(birthday="01-01")
        self.assertEqual(occasions_for(date(2026, 1, 1), cfg), ["birthday", "new_year"])

    def test_exam_week(self) -> None:
        cfg = ThemeConfig(exam_weeks=["01-10..01-20"])
        self.assertEqual(occasions_for(date(2026, 1, 10), cfg), ["exam_week"])
        self.assertEqual(occasions_for(date(2026, 1, 20), cfg), ["exam_week"])
        self.assertEqual(occasions_for(date(2026, 1, 21), cfg), [])
        self.assertEqual(occasions_for(date(2026, 2, 10), cfg), [])

    def test_bad_week_format_is_ignored_not_fatal(self) -> None:
        cfg = ThemeConfig(exam_weeks=["garbage", "01-10..01-20"])
        self.assertEqual(occasions_for(date(2026, 1, 10), cfg), ["exam_week"])

    def test_order_matches_priority(self) -> None:
        self.assertEqual(
            OCCASION_ORDER, ("birthday", "spring_festival", "new_year", "christmas", "exam_week")
        )


class ThemeConfigTest(unittest.TestCase):
    def test_defaults_off(self) -> None:
        cfg = _cfg()
        self.assertEqual(cfg.theme.birthday, "")
        self.assertEqual(cfg.theme.exam_weeks, [])

    def test_parses(self) -> None:
        cfg = _cfg(birthday="05-21", exam_weeks=["01-10..01-20"])
        self.assertEqual(cfg.theme.birthday, "05-21")
        self.assertEqual(cfg.theme.exam_weeks, ["01-10..01-20"])

    def test_bad_birthday_rejected(self) -> None:
        for bad in ("5月21日", "13-01", "05-32", "0521"):
            with self.assertRaises(ValueError, msg=bad):
                _cfg(birthday=bad)

    def test_bad_exam_week_rejected(self) -> None:
        for bad in ("01-10", "01-10...", "xx..yy"):
            with self.assertRaises(ValueError, msg=bad):
                _cfg(exam_weeks=[bad])

    def test_roundtrip_keeps_theme(self) -> None:
        cfg = _cfg(birthday="05-21", exam_weeks=["01-10..01-20"])
        raw = hub_config_to_raw(cfg)
        again = parse_config(raw)
        self.assertEqual(again.theme, cfg.theme)

    def test_empty_theme_drops_from_raw(self) -> None:
        cfg = _cfg()
        self.assertNotIn("theme", hub_config_to_raw(cfg))


if __name__ == "__main__":
    unittest.main()
