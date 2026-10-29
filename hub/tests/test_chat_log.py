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

    def test_none_path_is_noop(self) -> None:
        log = ChatLog(None)
        log.append(turn="x", user="hi", reply="yo")
        self.assertEqual(log.list(), [])
