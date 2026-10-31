"""F5 声纹：安卓认出谁在说话，hub 把称呼带进提示词和对话记录。"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from dock_hub.chat_log import ChatLog
from dock_hub.companion import _clean_speaker, speaker_prompt
from dock_hub.config import parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class CleanSpeakerTest(unittest.TestCase):
    def test_keeps_a_short_name(self) -> None:
        self.assertEqual(_clean_speaker("妈妈"), "妈妈")
        self.assertEqual(_clean_speaker(" 小雨 "), "小雨")
        self.assertEqual(_clean_speaker("妈妈。"), "妈妈")

    def test_caps_and_empties(self) -> None:
        self.assertEqual(_clean_speaker("一二三四五六七八九十甲乙丙"), "一二三四五六七八九十甲乙")
        self.assertEqual(_clean_speaker(None), "")
        self.assertEqual(_clean_speaker("。。。"), "")
        self.assertEqual(_clean_speaker(123), "123")


class SpeakerPromptTest(unittest.TestCase):
    def test_only_when_known(self) -> None:
        self.assertEqual(speaker_prompt(""), "")
        line = speaker_prompt("妈妈")
        self.assertIn("妈妈", line)
        self.assertIn("别每句都提", line)


class SpeakerLogTest(unittest.TestCase):
    def test_recorded_when_present_and_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ChatLog(Path(tmp) / "companion-chats.jsonl")
            log.append(turn="a", user="几点了", reply="七点半。", speaker="妈妈")
            log.append(turn="b", user="晚安", reply="晚安。")
            rows = log.list(limit=10)
            self.assertEqual(rows[0]["speaker"], "妈妈")
            self.assertIsNone(rows[1]["speaker"])


class SpeakerProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "companion": {
                    "enabled": True,
                    "llm": {
                        "base_url": "http://127.0.0.1:9",
                        "model": "qwen3.5:4b",
                        "timeout_sec": 2,
                    },
                },
                "devices": [],
                "mini": False,
            },
            path=Path(self.tmp.name) / "hub.yaml",
        )
        self.hub = DockHub(cfg)
        handler = make_handler(self.hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.log_path = Path(self.tmp.name) / "companion-chats.jsonl"

    def _post(self, body: dict) -> int:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        raw = json.dumps(body).encode("utf-8")
        conn.request(
            "POST",
            "/v1/companion/chat",
            body=raw,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token-value",
            },
        )
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status

    def _rows(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_known_speaker_is_recorded_on_canned_reply(self) -> None:
        status = self._post({"text": "几点了", "speaker": "妈妈"})
        self.assertEqual(status, 200)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["speaker"], "妈妈")

    def test_unknown_speaker_stays_empty(self) -> None:
        status = self._post({"text": "几点了"})
        self.assertEqual(status, 200)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["speaker"])

    def test_error_path_also_records_speaker(self) -> None:
        # LLM 没起：走错误路径，说话人也要跟账走
        status = self._post({"text": "给我讲个故事吧", "speaker": "爸爸"})
        self.assertEqual(status, 502)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["speaker"], "爸爸")


if __name__ == "__main__":
    unittest.main()
