from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dock_hub.chat_log import ChatLog


class ChatLogTest(unittest.TestCase):
    def test_round_trip_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ChatLog(Path(tmp) / "companion-chats.jsonl")
            log.append(turn="a", user="启动无畏契约", reply="好。", ms=12, skills=[{"id": "wegame", "ok": True}])
            log.append(turn="b", user="开灯", reply="灯下安静。", ms=8)
            found = log.list(query="无畏")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["user"], "启动无畏契约")
            self.assertEqual(found[0]["skills"][0]["id"], "wegame")
            all_items = log.list()
            self.assertEqual(len(all_items), 2)

    def test_day_filter_and_timeline(self) -> None:
        # F30 时间线：day 挑一天，days 给出记过话的日子（旧→新）
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion-chats.jsonl"
            lines = [
                '{"ts":"2026-10-29T21:00:00+08:00","turn":"a","user":"早","reply":"早。","ms":1,"skills":[]}',
                '{"ts":"2026-10-31T07:30:00+08:00","turn":"b","user":"几点","reply":"七点半。","ms":2,"skills":[]}',
                '{"ts":"2026-10-31T23:10:00+08:00","turn":"c","user":"晚安","reply":"晚安。","ms":3,"skills":[]}',
                "not-json-at-all",
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            log = ChatLog(path)
            self.assertEqual(log.days(), ["2026-10-29", "2026-10-31"])
            day = log.list(day="2026-10-31")
            self.assertEqual([item["turn"] for item in day], ["b", "c"])
            self.assertEqual(log.list(day="2026-10-30"), [])
            # day 和 query 叠加：这天里再挑字
            both = log.list(day="2026-10-31", query="晚安")
            self.assertEqual([item["turn"] for item in both], ["c"])

    def test_none_path_is_noop(self) -> None:
        log = ChatLog(None)
        log.append(turn="x", user="hi", reply="yo")
        self.assertEqual(log.list(), [])
        self.assertEqual(log.days(), [])
