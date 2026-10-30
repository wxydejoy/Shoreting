"""追问澄清（F3）：该问的问一句，答了就办，答不上认输，闲聊不误伤。"""

from __future__ import annotations

import time
import unittest

from dock_hub.clarify import (
    PendingClarify,
    TTL_SEC,
    clarify_for,
    take_answer,
)
from dock_hub.companion import Companion
from dock_hub.config import CompanionConfig
from dock_hub.intents import turn_intent

SKILLS = [
    {"id": "lamp", "name": "台灯", "kind": "toggle"},
    {"id": "desk-led", "name": "灯带", "kind": "toggle"},
    {"id": "wegame", "name": "无畏契约", "kind": "run"},
]


class TurnIntentTest(unittest.TestCase):
    def test_on_off_and_none(self) -> None:
        self.assertIs(turn_intent("打开台灯。"), True)
        self.assertIs(turn_intent("把它关掉"), False)
        self.assertIsNone(turn_intent("亮一点"))
        self.assertIsNone(turn_intent("开黑。"))  # 有开黑，但不是开关口吻
        self.assertIsNone(turn_intent("打开又马上关掉"))  # 态度不明不替人决定
        self.assertIsNone(turn_intent("今天好累"))


class ClarifyForTest(unittest.TestCase):
    def test_unresolved_switch_asks(self) -> None:
        pending = clarify_for("把它关掉", SKILLS)
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertFalse(pending.on)
        self.assertIn("关", pending.asked)
        self.assertIn("台灯", pending.asked)
        self.assertIn("灯带", pending.asked)

    def test_single_target_asks_yes_no(self) -> None:
        pending = clarify_for("帮我开一下那个", [{"id": "lamp", "name": "台灯", "kind": "toggle"}])
        assert pending is not None
        self.assertEqual(pending.asked, "要开台灯吗？")

    def test_resolved_commands_never_ask(self) -> None:
        self.assertIsNone(clarify_for("关台灯", SKILLS))
        self.assertIsNone(clarify_for("关掉灯带", SKILLS))
        self.assertIsNone(clarify_for("打开无畏契约", SKILLS))

    def test_brightness_temp_and_nounless_text_pass_through(self) -> None:
        self.assertIsNone(clarify_for("亮一点", SKILLS))  # 亮度是别人的活
        self.assertIsNone(clarify_for("空调调到二十度", SKILLS))
        self.assertIsNone(clarify_for("我要打瓦", SKILLS))  # 没带开/关态度
        self.assertIsNone(clarify_for("今天好累", SKILLS))

    def test_too_many_names_asks_short(self) -> None:
        many = [{"id": f"d{i}", "name": f"设备{i}", "kind": "toggle"} for i in range(5)]
        pending = clarify_for("把它关掉", many)
        assert pending is not None
        self.assertEqual(pending.asked, "要关哪个？")

    def test_no_skills_no_ask(self) -> None:
        self.assertIsNone(clarify_for("把它关掉", []))


class TakeAnswerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pending = PendingClarify(on=False, asked="要关哪个？台灯还是灯带？")

    def test_bare_name_runs(self) -> None:
        answer = take_answer("台灯", self.pending, SKILLS)
        assert answer is not None
        self.assertEqual(answer.kind, "run")
        self.assertEqual(answer.ident, "lamp")

    def test_nickname_runs(self) -> None:
        for word, ident in (("灯带", "desk-led"), ("氛围灯", "desk-led")):
            answer = take_answer(word, self.pending, SKILLS)
            assert answer is not None
            self.assertEqual((answer.kind, answer.ident), ("run", ident))

    def test_chatter_mentioning_device_does_not_run(self) -> None:
        # 「今天买了台灯」不是回答：动了灯就是瞎猜
        self.assertIsNone(take_answer("今天买了台灯", self.pending, SKILLS))

    def test_fresh_command_hands_over(self) -> None:
        # 整条新指令交给正常流程，结果一样
        self.assertIsNone(take_answer("关台灯", self.pending, SKILLS))

    def test_cancel(self) -> None:
        answer = take_answer("算了不用了", self.pending, SKILLS)
        assert answer is not None
        self.assertEqual(answer.kind, "cancel")

    def test_second_failure_admits_defeat(self) -> None:
        answer = take_answer("把它关掉", self.pending, SKILLS)
        assert answer is not None
        self.assertEqual(answer.kind, "miss")

    def test_topic_change_hands_over(self) -> None:
        self.assertIsNone(take_answer("今天好累", self.pending, SKILLS))


class PendingTtlTest(unittest.TestCase):
    def test_expires(self) -> None:
        pending = PendingClarify(on=False, asked="要关哪个？")
        self.assertFalse(pending.expired(time.monotonic()))
        self.assertTrue(pending.expired(time.monotonic() + TTL_SEC + 1))


class CompanionClarifyTest(unittest.TestCase):
    """端到端：问 → 答 → 执行，全程不碰 LLM。"""

    def _companion(self, runs: list[tuple[str, bool]]) -> Companion:
        comp = Companion(CompanionConfig(enabled=True))

        def run(ident: str, on: bool, **_: object) -> None:
            runs.append((ident, on))

        comp.set_skills([dict(item) for item in SKILLS], run)
        return comp

    def test_ask_then_answer_executes(self) -> None:
        runs: list[tuple[str, bool]] = []
        comp = self._companion(runs)
        asked = comp.chat({"text": "把它关掉", "id": "t1"})
        self.assertIn("哪个", asked["text"])
        self.assertEqual(runs, [])
        done = comp.chat({"text": "台灯", "id": "t2"})
        self.assertEqual(done["text"], "好。")
        self.assertEqual(runs, [("lamp", False)])

    def test_cancel_after_ask(self) -> None:
        runs: list[tuple[str, bool]] = []
        comp = self._companion(runs)
        comp.chat({"text": "把它关掉", "id": "t1"})
        self.assertEqual(comp.chat({"text": "算了", "id": "t2"})["text"], "好。")
        self.assertEqual(runs, [])

    def test_second_failure_says_unclear_and_drops_pending(self) -> None:
        runs: list[tuple[str, bool]] = []
        comp = self._companion(runs)
        comp.chat({"text": "把它关掉", "id": "t1"})
        self.assertEqual(comp.chat({"text": "把它关掉", "id": "t2"})["text"], "没听清。")
        self.assertIsNone(comp._pending_clarify)
        self.assertEqual(runs, [])

    def test_expired_pending_asks_again(self) -> None:
        runs: list[tuple[str, bool]] = []
        comp = self._companion(runs)
        comp.chat({"text": "把它关掉", "id": "t1"})
        assert comp._pending_clarify is not None
        comp._pending_clarify.at -= TTL_SEC + 1
        asked = comp.chat({"text": "把它关掉", "id": "t2"})
        self.assertIn("哪个", asked["text"])

    def test_run_failure_reports(self) -> None:
        from dock_hub.errors import HubError

        comp = Companion(CompanionConfig(enabled=True))

        def run(ident: str, on: bool, **_: object) -> None:
            raise HubError("device_error", "灯没应答")

        comp.set_skills([dict(item) for item in SKILLS], run)
        comp.chat({"text": "把它关掉", "id": "t1"})
        done = comp.chat({"text": "台灯", "id": "t2"})
        self.assertEqual(done["text"], "这次没动成。")


if __name__ == "__main__":
    unittest.main()
