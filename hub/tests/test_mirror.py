"""F35 通知镜像：去重、上限、路由鉴权、进 snapshot。"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from dock_hub.mirror import MirrorBoard
from dock_hub.ratelimit import Gate


def _at(hour: int, minute: int = 0) -> datetime:
    today = datetime.now().astimezone().date()
    return datetime(today.year, today.month, today.day, hour, minute).astimezone()


class MirrorBoardTest(unittest.TestCase):
    def test_post_returns_item_with_id(self) -> None:
        board = MirrorBoard()
        item = board.post("com.tencent.mm", "妈妈：饭好了", "微信", now=_at(18))
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["app"], "com.tencent.mm")
        self.assertEqual(item["title"], "微信")
        self.assertEqual(board.snapshot()["last_id"], 1)

    def test_same_message_within_a_minute_is_duplicate(self) -> None:
        board = MirrorBoard()
        first = board.post("app.x", "渲染完了", "Task", now=_at(18))
        self.assertIsNone(board.post("app.x", "渲染完了", "Task", now=_at(18) + timedelta(seconds=30)))
        again = board.post("app.x", "渲染完了", "Task", now=_at(18, 1))
        self.assertEqual(again["id"], first["id"] + 1)

    def test_repost_after_gap_is_new(self) -> None:
        board = MirrorBoard()
        board.post("app.x", "下载 1%", "Download", now=_at(18))
        again = board.post("app.x", "下载 1%", "Download", now=_at(18, 2))
        self.assertEqual(again["id"], 2)

    def test_empty_app_or_text_rejected(self) -> None:
        board = MirrorBoard()
        with self.assertRaises(ValueError):
            board.post("  ", "正文")
        with self.assertRaises(ValueError):
            board.post("app.x", "   ")

    def test_clipped_to_caps(self) -> None:
        board = MirrorBoard()
        item = board.post("a" * 200, "字" * 500, "标" * 200, now=_at(18))
        self.assertLessEqual(len(item["app"]), 80)
        self.assertLessEqual(len(item["text"]), 160)
        self.assertLessEqual(len(item["title"]), 60)

    def test_capped_and_pruned(self) -> None:
        board = MirrorBoard()
        base = _at(8)
        for index in range(9):
            board.post("app.x", f"第{index}条", now=base + timedelta(minutes=index))
        snap = board.snapshot()
        self.assertEqual(len(snap["items"]), 5)
        self.assertEqual(snap["items"][0]["text"], "第4条")
        self.assertEqual(snap["last_id"], 9)


class MirrorGateTest(unittest.TestCase):
    def test_mirror_bucket_exists(self) -> None:
        from dock_hub.config import LimitsConfig

        gate = Gate.from_config(LimitsConfig())
        self.assertIsNotNone(gate)
        for _ in range(35):
            wait = gate.check("mirror", "10.0.0.1")
            if wait > 0:
                break
        self.assertGreater(wait, 0)


if __name__ == "__main__":
    unittest.main()
