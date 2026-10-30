"""persona.py：她怎么说、怎么记事。

同样只从 dock_hub.persona 取东西。这一层回答的是「今晚几点、屋里几度」该
怎么说出口，以及她自己的情绪什么时候该收着——都能用假数据直接验，不用起 Hub。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from dock_hub.persona import (
    ack_after_skills,
    allow_ask,
    appraise_user,
    cn_clock,
    cn_num,
    decay_mood,
    default_mood,
    enforce_ask_budget,
    load_mood,
    mood_prompt,
    next_self,
    period_label,
    recent_prompt,
    save_mood,
    speak_light,
    speak_media,
    speak_mini,
    speak_pc,
    speak_temp,
    speak_time,
    speak_timer,
    unsolicited_block_reason,
)


class ClockWordsTest(unittest.TestCase):
    def test_cn_num(self) -> None:
        self.assertEqual(cn_num(0), "零")
        self.assertEqual(cn_num(10), "十")
        self.assertEqual(cn_num(11), "十一")
        self.assertEqual(cn_num(27), "二十七")
        self.assertEqual(cn_num(120), "120")

    def test_cn_clock_drops_leading_zero_minute(self) -> None:
        self.assertEqual(cn_clock(9, 0), "九点")
        self.assertEqual(cn_clock(9, 5), "九点零五")
        self.assertEqual(cn_clock(21, 30), "二十一点三十")

    def test_period_label_covers_the_day(self) -> None:
        self.assertEqual(period_label(7), "清晨")
        self.assertEqual(period_label(14), "午后")
        self.assertEqual(period_label(20), "夜里")
        self.assertEqual(period_label(2), "深夜")


class EmotionTest(unittest.TestCase):
    def test_appraise(self) -> None:
        self.assertEqual(appraise_user("小岸"), "greeting")
        self.assertEqual(appraise_user("今天好累"), "tired")
        self.assertEqual(appraise_user("烦死了"), "frustrated")
        self.assertEqual(appraise_user("哈哈好的"), "bright")
        self.assertEqual(appraise_user("把那本书递给我"), "calm")

    def test_late_night_makes_her_concerned(self) -> None:
        self.assertEqual(next_self("quiet", "calm", 1), "concerned")
        self.assertEqual(next_self("quiet", "bright", 23), "warm")

    def test_concerned_lingers_one_turn(self) -> None:
        # 已经在担心，就再担心一轮；但对方心情好就不用端着
        self.assertEqual(next_self("concerned", "calm", 15), "concerned")
        self.assertEqual(next_self("concerned", "bright", 15), "warm")

    def test_ask_is_limited(self) -> None:
        mood = default_mood()
        self.assertTrue(allow_ask(mood, control=False))
        self.assertFalse(allow_ask(mood, control=True))
        self.assertFalse(allow_ask({**mood, "last_ask": True}, control=False))
        self.assertFalse(allow_ask({**mood, "user": "frustrated"}, control=False))

    def test_mood_fades_after_an_hour(self) -> None:
        now = datetime(2026, 10, 30, 21, 0, tzinfo=datetime.now().astimezone().tzinfo)
        fresh = {"user": "tired", "self": "concerned", "updated_at": (now - timedelta(minutes=5)).isoformat()}
        self.assertEqual(decay_mood(fresh, now)["self"], "concerned")
        stale = {**fresh, "updated_at": (now - timedelta(hours=2)).isoformat()}
        faded = decay_mood(stale, now)
        self.assertEqual(faded["self"], "quiet")
        self.assertEqual(faded["user"], "calm")

    def test_mood_prompt_says_dont_ask(self) -> None:
        text = mood_prompt(default_mood(), 21)
        self.assertIn("夜里", text)
        self.assertIn("不要提问", text)

    def test_unsolicited_is_blocked_when_disconnected_or_gaming(self) -> None:
        self.assertEqual(unsolicited_block_reason(connected=False, game=False), "disconnected")
        self.assertEqual(unsolicited_block_reason(connected=True, game=True), "game")
        self.assertIsNone(unsolicited_block_reason(connected=True, game=False))


class MoodFileTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mood.yaml"
            save_mood(path, {"user": "tired", "self": "concerned", "last_ask": True})
            back = load_mood(path)
            self.assertEqual(back["user"], "tired")
            self.assertEqual(back["self"], "concerned")
            self.assertTrue(back["last_ask"])

    def test_unknown_labels_fall_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mood.yaml"
            path.write_text("user: 发火\nself: 飘\n", encoding="utf-8")
            back = load_mood(path)
            self.assertEqual(back["user"], "calm")
            self.assertEqual(back["self"], "quiet")

    def test_missing_or_broken_file_gives_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mood.yaml"
            self.assertEqual(load_mood(path), default_mood())
            path.write_text("这不是 yaml: [", encoding="utf-8")
            self.assertEqual(load_mood(path), default_mood())


class SpokenCopyTest(unittest.TestCase):
    def test_time(self) -> None:
        now = datetime(2026, 10, 30, 21, 5)
        self.assertEqual(speak_time(now, "quiet", "现在几点"), "现在是二十一点零五。")
        self.assertEqual(speak_time(now, "quiet", "今天星期几"), "今天星期五。")
        self.assertEqual(speak_time(now, "quiet", "今天几号"), "今天十月三十号。")
        self.assertEqual(speak_time(now, "concerned", "几点"), "已经二十一点零五了。")

    def test_temp(self) -> None:
        self.assertEqual(speak_temp({"celsius": 23.4}, "屋里多少度"), "屋里23.4度。")
        self.assertEqual(speak_temp({"celsius": 23, "humidity": 82}, "潮不潮"), "屋里23度。有一点潮。")
        self.assertEqual(speak_temp({"celsius": 23, "humidity": 50}, "潮不潮"), "屋里23度。还不潮。")
        self.assertEqual(speak_temp(None, "多少度"), "我这边还没有室温。")

    def test_pc_offline_and_busy(self) -> None:
        self.assertEqual(speak_pc(None, "电脑占用"), "电脑那边还没连上。")
        idle = {"online": True, "cpu": {"percent": 12}}
        self.assertEqual(speak_pc(idle, "电脑"), "电脑还不热。")
        busy = {"online": True, "cpu": {"percent": 91}}
        self.assertEqual(speak_pc(busy, "电脑忙吗"), "电脑挺忙的，CPU 91%。")

    def test_pc_memory_and_temperature(self) -> None:
        pc = {"online": True, "cpu": {"percent": 10}, "memory": {"percent": 63, "used_gb": 10.2}}
        self.assertEqual(speak_pc(pc, "内存用了多少"), "内存63%，用了10.2G。")
        hot = {"online": True, "cpu": {"percent": 10, "temp_celsius": 78}}
        self.assertEqual(speak_pc(hot, "电脑多少度"), "电脑现在CPU 78度。")

    def test_mini_reads_from_the_desk_payload(self) -> None:
        desk = {"mini": {"pc": {"cpu": {"percent": 8}}}}
        self.assertEqual(speak_mini(desk, "mini 忙吗"), "Mini还不热。")
        self.assertEqual(speak_mini({}, "mini"), "Mini 那边还没报到。")

    def test_media(self) -> None:
        self.assertEqual(speak_media({"playing": False}), "现在没在放。")
        playing = {"playing": True, "title": "夜航", "artist": "某乐队"}
        self.assertIn("夜航", speak_media(playing))

    def test_timer(self) -> None:
        self.assertEqual(speak_timer(1500), "好，二十五分钟后叫你。")

    def test_light_uses_the_getter(self) -> None:
        skills = [{"id": "lamp", "name": "台灯"}]
        on = speak_light("台灯开着吗", skills, lambda _id: {"on": True, "brightness": 60})
        self.assertEqual(on, "台灯亮着，亮度60。")
        off = speak_light("灯关了吗", skills, lambda _id: {"on": False})
        self.assertEqual(off, "台灯关着。")
        unknown = speak_light("灯", skills, lambda _id: None)
        self.assertEqual(unknown, "我不确定灯现在开没开。")


class ReplyTrimTest(unittest.TestCase):
    def test_question_is_dropped_when_not_allowed(self) -> None:
        self.assertEqual(enforce_ask_budget("灯关了。要读书吗？", allow=False), "灯关了。")
        self.assertEqual(enforce_ask_budget("灯关了。要读书吗？", allow=True), "灯关了。要读书吗？")
        # 全是问句时给一句兜底，别回空字符串
        self.assertEqual(enforce_ask_budget("要读书吗？", allow=False), "嗯。")

    def test_failed_action_is_not_acknowledged_as_done(self) -> None:
        events = [{"ok": False}]
        self.assertEqual(ack_after_skills("好。", events), "这次没动成。")
        self.assertEqual(ack_after_skills("好。", [{"ok": True}]), "好。")
        self.assertEqual(ack_after_skills("灯亮着。", []), "灯亮着。")

    def test_recent_prompt_keeps_the_last_turns_short(self) -> None:
        turns = [("用户", "很长的回答" * 20), ("用户二", "短")]
        text = recent_prompt(turns)
        self.assertIn("用户二", text)
        self.assertIn("…", text)


class IndependenceTest(unittest.TestCase):
    def test_persona_does_not_drag_in_the_companion(self) -> None:
        code = "import sys, dock_hub.persona; print('dock_hub.companion' in sys.modules)"
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "False", out.stderr)

    def test_companion_still_reexports_the_old_names(self) -> None:
        from dock_hub import companion

        self.assertIs(companion.speak_temp, speak_temp)
        self.assertIs(companion.load_mood, load_mood)


if __name__ == "__main__":
    unittest.main()
