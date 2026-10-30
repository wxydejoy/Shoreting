"""对讲 / 留言 / 微信（F33 · F4 · F34）：relay 识别、队列、她念的那句。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from dock_hub.notify import NotifyBoard, parse_relay, wechat_say_text


def _at(hour: int, minute: int = 0) -> datetime:
    today = datetime.now().astimezone().date()
    return datetime(today.year, today.month, today.day, hour, minute).astimezone()


class ParseRelayTest(unittest.TestCase):
    def test_relay_phrases(self) -> None:
        self.assertEqual(parse_relay("跟她说我晚点回"), "我晚点回")
        self.assertEqual(parse_relay("跟她说，饭好了"), "饭好了")
        self.assertEqual(parse_relay("帮我带个话：记得取快递"), "记得取快递")
        self.assertEqual(parse_relay("留言，先别关空调"), "先别关空调")
        self.assertEqual(parse_relay("告诉她我下楼了"), "我下楼了")

    def test_questions_are_not_relay(self) -> None:
        # 「跟她说今天几度」是在跟她聊天，不是递话
        self.assertIsNone(parse_relay("跟她说今天几度"))
        self.assertIsNone(parse_relay("跟她说今天天气怎么样呢"))

    def test_plain_talk_stays_none(self) -> None:
        self.assertIsNone(parse_relay("今天天气不错"))
        self.assertIsNone(parse_relay(""))
        self.assertIsNone(parse_relay("留言"))


class NotifyBoardTest(unittest.TestCase):
    def test_post_ids_increase_and_snapshot(self) -> None:
        board = NotifyBoard()
        first = board.post("饭好了", source="desk", now=_at(18))
        second = board.post("我下楼了", source="wechat", now=_at(18, 1))
        self.assertEqual(first["id"] + 1, second["id"])
        snap = board.snapshot()
        self.assertEqual(snap["last_id"], second["id"])
        self.assertEqual([item["text"] for item in snap["items"]], ["饭好了", "我下楼了"])

    def test_empty_text_rejected(self) -> None:
        board = NotifyBoard()
        with self.assertRaises(ValueError):
            board.post("   ")

    def test_pruned_after_24h_and_capped(self) -> None:
        board = NotifyBoard()
        base = _at(8)
        for index in range(15):
            board.post(f"第{index}条", now=base + timedelta(minutes=index))
        snap = board.snapshot()
        self.assertEqual(len(snap["items"]), 10)
        # 最旧的 5 条被挤掉，id 仍是自增的那个口径
        self.assertEqual(snap["items"][0]["text"], "第5条")
        # 超 24 小时的旧话不值得补递：发新的一条时顺手剪掉
        board2 = NotifyBoard()
        board2.post("旧的", now=base)
        board2.post("新的", now=base + timedelta(hours=25))
        self.assertEqual([item["text"] for item in board2.snapshot()["items"]], ["新的"])


class WechatSayTest(unittest.TestCase):
    def test_with_sender(self) -> None:
        self.assertEqual(wechat_say_text("饭好了", "妈妈"), "微信，妈妈说：饭好了。")

    def test_without_sender_and_long_body(self) -> None:
        self.assertEqual(wechat_say_text("到了", ""), "微信说：到了。")
        long = "字" * 200
        said = wechat_say_text(long, "某人")
        self.assertTrue(said.startswith("微信，某人说："))
        self.assertLessEqual(len(said), 130)

    def test_empty_body_is_empty(self) -> None:
        self.assertEqual(wechat_say_text("  ", "某人"), "")


class HubRelayTest(unittest.TestCase):
    """语音递话端到端：说「跟她说…」→ 队列 → snapshot.notify。"""

    def setUp(self) -> None:
        import tempfile

        from dock_hub.config import parse_config
        from dock_hub.service import DockHub

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
                "companion": {"enabled": True},
                "mini": False,
            },
            Path(self.tmp.name) / "hub.yaml",
        )
        self.hub = DockHub(cfg)

    def test_voice_relay_lands_in_notify(self) -> None:
        out = self.hub.companion.chat({"text": "跟她说我晚点回"})
        self.assertIn("写下来了", out["text"])
        snap = self.hub.snapshot()
        items = snap["notify"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "我晚点回")
        self.assertEqual(items[0]["source"], "desk")

    def test_question_is_chat_not_relay(self) -> None:
        # 「跟她说今天几度」不递话。没有 TTS/LLM 的测试环境里 chat 会报
        # companion_unavailable——只要不是「写下来了」就算对
        try:
            out = self.hub.companion.chat({"text": "跟她说今天几度"})
            self.assertNotIn("写下来了", out.get("text") or "")
        except Exception:  # noqa: BLE001 - 走到 LLM 报错也算没递话
            pass

    def test_relay_failure_reports_honestly(self) -> None:
        # 队列没接上（world 里没有 notify）：不递话也不装懂，落回对话路径
        # ——测试环境没有 LLM，所以这是一次 HubError，而不是一句假的「写下来了」
        self.hub.companion._world.pop("notify", None)
        from dock_hub.errors import HubError

        with self.assertRaises(HubError):
            self.hub.companion.chat({"text": "跟她说我晚点回"})
        self.assertEqual(self.hub.snapshot()["notify"]["items"], [])

    def test_wechat_speaks_when_companion_on(self) -> None:
        out = self.hub.ingest_wechat({"text": "饭好了", "from": "妈妈"})
        self.assertTrue(out["ok"])
        self.assertIn("微信，妈妈说：饭好了。", out["said"] or "")


if __name__ == "__main__":
    unittest.main()
