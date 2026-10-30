"""看得见的倒计时（F9）：状态板、正计时口令、snapshot 里的 timer 字段。"""

import unittest
from unittest.mock import patch

from dock_hub.config import parse_config
from dock_hub.intents import parse_stopwatch_action, parse_stopwatch_start, parse_stopwatch_stop
from dock_hub.timers import TimerBoard, speak_stopwatch_result


class FakeMono:
    """可手拨的单调钟。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TimerBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mono = FakeMono()
        self.board = TimerBoard()
        patcher = patch("dock_hub.timers.time.monotonic", self.mono)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_is_none(self) -> None:
        self.assertIsNone(self.board.snapshot())

    def test_countdown_counts_down(self) -> None:
        self.board.arm(25 * 60, "番茄钟")
        body = self.board.snapshot()
        self.assertEqual(body["mode"], "timer")
        self.assertEqual(body["total_sec"], 1500)
        self.assertEqual(body["seconds_left"], 1500)
        self.mono.now += 70
        self.assertEqual(self.board.snapshot()["seconds_left"], 1430)

    def test_countdown_bottoms_out_at_zero(self) -> None:
        self.board.arm(60)
        self.mono.now += 3600
        self.assertEqual(self.board.snapshot()["seconds_left"], 0)

    def test_rearm_replaces(self) -> None:
        self.board.arm(60)
        self.board.arm(300)
        self.assertEqual(self.board.snapshot()["total_sec"], 300)

    def test_clear_empties(self) -> None:
        self.board.arm(60)
        self.board.clear()
        self.assertIsNone(self.board.snapshot())

    def test_stopwatch_counts_up(self) -> None:
        self.board.start_stopwatch("写周报")
        body = self.board.snapshot()
        self.assertEqual(body["mode"], "stopwatch")
        self.assertEqual(body["label"], "写周报")
        self.mono.now += 95
        self.assertEqual(self.board.snapshot()["seconds"], 95)

    def test_stop_returns_elapsed_then_none(self) -> None:
        self.board.start_stopwatch()
        self.mono.now += 125
        self.assertEqual(self.board.stop_stopwatch(), 125)
        self.assertIsNone(self.board.stop_stopwatch())
        self.assertIsNone(self.board.snapshot())

    def test_stop_without_start(self) -> None:
        self.assertIsNone(self.board.stop_stopwatch())

    def test_countdown_and_stopwatch_coexist(self) -> None:
        self.board.arm(60)
        self.board.start_stopwatch()
        self.assertEqual(self.board.snapshot()["mode"], "timer")
        self.board.clear()
        self.assertEqual(self.board.snapshot()["mode"], "stopwatch")


class SpeakStopwatchResultTest(unittest.TestCase):
    def test_buckets(self) -> None:
        self.assertEqual(speak_stopwatch_result(45), "记了 45 秒。")
        self.assertEqual(speak_stopwatch_result(90), "记了 2 分钟。")
        self.assertEqual(speak_stopwatch_result(3600), "记了 1 个小时。")
        self.assertEqual(speak_stopwatch_result(3900), "记了 1 小时 5 分钟。")


class StopwatchIntentTest(unittest.TestCase):
    def test_start_phrases(self) -> None:
        for text in ("开始正计时", "开始计时", "帮我启动计时器", "来一个正计时"):
            self.assertTrue(parse_stopwatch_start(text), text)
            self.assertFalse(parse_stopwatch_stop(text))

    def test_stop_phrases(self) -> None:
        for text in ("停止计时", "结束正计时", "计时结束", "停掉计时器"):
            self.assertTrue(parse_stopwatch_stop(text), text)
            self.assertFalse(parse_stopwatch_start(text))

    def test_unrelated_text(self) -> None:
        self.assertFalse(parse_stopwatch_start("二十五分钟之后叫我"))
        self.assertFalse(parse_stopwatch_stop("几点了"))

    def test_action_form(self) -> None:
        self.assertEqual(parse_stopwatch_action("ACTION: stopwatch.start"), "start")
        self.assertEqual(parse_stopwatch_action("ACTION: stopwatch.stop"), "stop")
        self.assertIsNone(parse_stopwatch_action("ACTION: timer.25"))


class CompanionSnapshotTimerTest(unittest.TestCase):
    """snapshot 带 timer；到点清掉后消失。"""

    def _companion(self):
        from dock_hub.config import CompanionConfig
        from dock_hub.companion import Companion

        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "devices": [],
                "companion": {"enabled": True},
            }
        )
        return Companion(cfg.companion)

    def test_snapshot_includes_timer(self) -> None:
        companion = self._companion()
        self.assertIsNone(companion.snapshot()["timer"])
        companion.timers.arm(90)
        body = companion.snapshot()
        self.assertEqual(body["timer"]["mode"], "timer")
        self.assertEqual(body["timer"]["seconds_left"], 90)

    def test_arm_then_cancel(self) -> None:
        companion = self._companion()
        companion._arm_timer(60)
        self.assertEqual(companion.snapshot()["timer"]["total_sec"], 60)
        companion._cancel_timer()
        self.assertIsNone(companion.snapshot()["timer"])

    def test_fire_clears(self) -> None:
        companion = self._companion()
        companion._cue_sentence = lambda *args, **kwargs: None  # 到点开口不在这验
        companion._arm_timer(1)
        companion._timer_fire()
        self.assertIsNone(companion.snapshot()["timer"])

    def test_handle_desk_stopwatch_flow(self) -> None:
        companion = self._companion()
        spoken, events = companion._handle_desk("开始正计时", "t1")
        self.assertEqual(spoken, "好，开始计时。")
        self.assertEqual(events[0]["id"], "stopwatch")
        self.assertEqual(companion.snapshot()["timer"]["mode"], "stopwatch")
        spoken, events = companion._handle_desk("停止计时", "t2")
        self.assertTrue(spoken.startswith("记了 "))
        self.assertEqual(events[0]["action"], "stop")

    def test_stop_without_start_is_honest(self) -> None:
        companion = self._companion()
        spoken, events = companion._handle_desk("停止计时", "t1")
        self.assertEqual(spoken, "还没开始记呢。")
        self.assertFalse(events[0]["ok"])


if __name__ == "__main__":
    unittest.main()
