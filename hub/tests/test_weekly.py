"""F27 桌面周报：台账、聚合、周日那一句、/week 页。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dock_hub.config import parse_config
from dock_hub.weekly import (
    DESK_INTERVAL_CAP_SEC,
    DailyLedger,
    summarize,
    weekly_line,
)


def _now(hour: int, minute: int = 0, weekday: int | None = None) -> datetime:
    """造一个确定的本周时刻（周一=1…周日=7）。"""
    today = date.today()
    shift = (today.isoweekday() - (weekday or today.isoweekday())) % 7
    day = today - timedelta(days=shift)
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


class SummarizeTest(unittest.TestCase):
    def _rows(self) -> dict[str, dict]:
        today = date.today()
        row_today = {
            "date": today.isoformat(),
            "desk_sec": 3600,
            "read_sec": 600,
            "chats": 8,
            "boots": 2,
            "last_at": "23:40",
            "first_at": "08:00",
        }
        old = today - timedelta(days=10)
        row_old = {"date": old.isoformat(), "desk_sec": 7200, "chats": 4}
        return {row["date"]: row for row in (row_today, row_old)}

    def test_window_is_seven_days_ending_today(self) -> None:
        out = summarize(self._rows())
        self.assertEqual(len(out["days"]), 7)
        self.assertEqual(out["days"][-1]["date"], date.today().isoformat())

    def test_totals_and_delta(self) -> None:
        out = summarize(self._rows())
        self.assertEqual(out["total"]["desk_sec"], 3600)
        self.assertEqual(out["total"]["chats"], 8)
        self.assertEqual(out["total"]["boots"], 2)
        # 10 天前那份落在「上一个 7 天」里，delta = 3600 - 7200
        self.assertEqual(out["delta"]["desk_sec"], 3600 - 7200)
        self.assertEqual(out["latest_at"], "23:40")

    def test_missing_days_are_zero(self) -> None:
        out = summarize({})
        self.assertEqual(out["total"], {"desk_sec": 0, "read_sec": 0, "chats": 0, "boots": 0})
        self.assertIsNone(out["latest_at"])


class WeeklyLineTest(unittest.TestCase):
    def test_empty_ledger_stays_silent(self) -> None:
        self.assertEqual(weekly_line(summarize({})), "")

    def test_line_mentions_desk_chats_latest(self) -> None:
        today = date.today()
        rows = {
            today.isoformat(): {
                "date": today.isoformat(),
                "desk_sec": 6 * 3600,
                "read_sec": 20 * 60,
                "chats": 34,
                "boots": 3,
                "last_at": "23:52",
            }
        }
        line = weekly_line(summarize(rows))
        self.assertIn("6 个小时", line)
        self.assertIn("34 句", line)
        self.assertIn("23:52", line)
        self.assertIn("20 分钟", line)

    def test_delta_gets_a_word(self) -> None:
        today = date.today()
        rows = {today.isoformat(): {"date": today.isoformat(), "desk_sec": 5 * 3600}}
        line = weekly_line(summarize(rows))
        self.assertIn("比上周", line)


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "companion-stats.jsonl"

    def test_notes_land_and_reload(self) -> None:
        ledger = DailyLedger(self.path)
        ledger.note_chat(_now(9))
        ledger.note_read(90, _now(10))
        ledger.note_boot(_now(8))
        ledger.note_desk(True, _now(9))
        ledger.note_desk(False, _now(11))
        again = DailyLedger(self.path)
        out = again.summary()
        self.assertEqual(out["total"]["chats"], 1)
        self.assertEqual(out["total"]["read_sec"], 90)
        self.assertEqual(out["total"]["boots"], 1)
        self.assertGreaterEqual(out["total"]["desk_sec"], 7000)

    def test_desk_interval_capped(self) -> None:
        ledger = DailyLedger(None)
        started = _now(1)
        ledger.note_desk(True, started)
        # 状态卡死（没人发离座）：结算最多记 2 小时
        ledger._settle_desk(started + timedelta(hours=9))
        self.assertEqual(ledger.summary()["total"]["desk_sec"], DESK_INTERVAL_CAP_SEC)

    def test_tick_reanchors_long_session(self) -> None:
        ledger = DailyLedger(None)
        started = _now(1)
        ledger.note_desk(True, started)
        ledger.tick(started + timedelta(seconds=DESK_INTERVAL_CAP_SEC + 60))
        out = ledger.summary()
        # 第一段顶格记 2 小时，第二段（1 分钟）还开着，被 summary 虚拟计入
        self.assertGreaterEqual(out["total"]["desk_sec"], DESK_INTERVAL_CAP_SEC)

    def test_summary_does_not_mutate(self) -> None:
        ledger = DailyLedger(None)
        started = _now(1)
        ledger.note_desk(True, started)
        first = ledger.summary()["total"]["desk_sec"]
        second = ledger.summary()["total"]["desk_sec"]
        self.assertEqual(first, second)

    def test_old_rows_pruned(self) -> None:
        ledger = DailyLedger(self.path)
        ancient = _now(9) - timedelta(days=200)
        with ledger._lock:
            ledger._row_for(ancient)
            ledger._save()
        again = DailyLedger(self.path)
        self.assertNotIn(ancient.date().isoformat(), again._rows)

    def test_bad_lines_skipped(self) -> None:
        today = date.today().isoformat()
        self.path.write_text(
            "{broken json\n\n" + json.dumps({"date": today, "chats": 3}) + "\n",
            encoding="utf-8",
        )
        ledger = DailyLedger(self.path)
        self.assertEqual(ledger._rows.get(today, {}).get("chats"), 3)


if __name__ == "__main__":
    unittest.main()
