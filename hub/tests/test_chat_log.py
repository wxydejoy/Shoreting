from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dock_hub.chat_log import TAIL_BLOCK, ChatLog


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

    def test_large_file_reads_only_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion-chats.jsonl"
            filler = "垫" * 200
            lines = [
                json.dumps({"turn": str(i), "user": filler, "reply": filler}, ensure_ascii=False)
                for i in range(4000)
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertGreater(path.stat().st_size, TAIL_BLOCK * 4)

            log = ChatLog(path)
            tail = log._tail_text(3)
            self.assertLess(len(tail.encode("utf-8")), path.stat().st_size // 2)
            self.assertTrue(tail.rstrip("\n").endswith(lines[-1]))

            items = log.list(limit=3)
            self.assertEqual([item["turn"] for item in items], ["3997", "3998", "3999"])

    def test_tail_of_file_without_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion-chats.jsonl"
            filler = "垫" * 200
            lines = [
                json.dumps({"turn": str(i), "user": filler, "reply": filler}, ensure_ascii=False)
                for i in range(2000)
            ]
            path.write_text("\n".join(lines), encoding="utf-8")

            log = ChatLog(path)
            items = log.list(limit=2)
            self.assertEqual([item["turn"] for item in items], ["1998", "1999"])

    def test_search_still_scans_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion-chats.jsonl"
            filler = "垫" * 200
            rows = [{"turn": str(i), "user": filler, "reply": filler} for i in range(3000)]
            rows[5] = {"turn": "old", "user": "很久以前开过灯", "reply": "嗯。"}
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            log = ChatLog(path)
            found = log.list(limit=5, query="开过灯")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["turn"], "old")

    def test_query_bypasses_tail_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ChatLog(Path(tmp) / "companion-chats.jsonl")
            log.append(turn="a", user="开灯", reply="好。")
            with patch.object(ChatLog, "_tail_text", side_effect=AssertionError("不该尾读")):
                self.assertEqual(len(log.list(query="开灯")), 1)
