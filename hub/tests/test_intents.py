"""intents.py：听懂一句话的那一层。

这里的用例刻意**只**从 dock_hub.intents 取东西，不从 companion 取：
只要有一条测试得绕回 companion 才能跑，就说明这一层还没真的独立出来。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from dock_hub.intents import (
    constrain_light_actions,
    infer_media,
    infer_volume,
    fold_game_asr,
    infer_brightness,
    infer_control,
    infer_noise,
    infer_temp,
    normalize_phrase,
    parse_bri_actions,
    parse_cn_int,
    parse_media_action,
    parse_teach,
    parse_timer,
    parse_timer_action,
    parse_volume_action,
    query_kind,
    resolve_skill,
    skill_prompt,
)

SKILLS = [
    {"id": "lamp", "name": "台灯", "kind": "light", "brightness": True},
    {"id": "desk-led", "name": "灯带", "kind": "light", "brightness": True},
    {"id": "ac", "name": "空调", "kind": "climate", "temp": True, "temp_min": 16, "temp_max": 30},
    {"id": "wegame", "name": "无畏契约", "kind": "run"},
]


class ParseCnIntTest(unittest.TestCase):
    def test_arabic_beats_chinese(self) -> None:
        self.assertEqual(parse_cn_int("80"), 80)
        self.assertEqual(parse_cn_int("调到 30 吧"), 30)

    def test_chinese_numbers(self) -> None:
        self.assertEqual(parse_cn_int("八"), 8)
        self.assertEqual(parse_cn_int("十"), 10)
        self.assertEqual(parse_cn_int("十五"), 15)
        self.assertEqual(parse_cn_int("二十"), 20)
        self.assertEqual(parse_cn_int("三十七"), 37)
        self.assertEqual(parse_cn_int("百"), 100)
        # 「两」当二用；「两个」这种带量词的不认（调用方传进来的是捕获组里的数字部分）
        self.assertEqual(parse_cn_int("两"), 2)
        self.assertIsNone(parse_cn_int("两个"))

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(parse_cn_int("亮一点"))
        self.assertIsNone(parse_cn_int(""))


class ControlTest(unittest.TestCase):
    def test_ledstrip_never_means_the_lamp(self) -> None:
        self.assertEqual(infer_control("关掉灯带。", SKILLS), [("desk-led", False)])
        self.assertEqual(infer_control("把灯带打开", SKILLS), [("desk-led", True)])

    def test_lamp_and_ac(self) -> None:
        self.assertEqual(infer_control("关灯。", SKILLS), [("lamp", False)])
        self.assertEqual(infer_control("打开空调。", SKILLS), [("ac", True)])

    def test_launch_word_maps_to_game(self) -> None:
        self.assertEqual(infer_control("我想打瓦", SKILLS), [("wegame", True)])

    def test_chitchat_is_not_a_command(self) -> None:
        self.assertEqual(infer_control("今天有点累。", SKILLS), [])

    def test_alias_wins_over_everything_else(self) -> None:
        aliases = {"开黑": {"id": "wegame", "on": True}}
        self.assertEqual(infer_control("开黑", SKILLS, aliases), [("wegame", True)])

    def test_constrain_drops_the_wrong_light(self) -> None:
        wanted = [("lamp", False), ("desk-led", True)]
        self.assertEqual(constrain_light_actions("关掉灯带。", wanted, SKILLS), [("desk-led", True)])


class BrightnessAndTempTest(unittest.TestCase):
    def test_relative_and_absolute(self) -> None:
        self.assertEqual(infer_brightness("亮一点", SKILLS), [("lamp", {"delta": 15})])
        self.assertEqual(infer_brightness("调暗一点", SKILLS), [("lamp", {"delta": -15})])
        self.assertEqual(infer_brightness("亮度调到三十", SKILLS), [("lamp", {"value": 30})])

    def test_temperature_talk_is_not_brightness(self) -> None:
        self.assertEqual(infer_brightness("空调调到26度", SKILLS), [])

    def test_temp_is_clamped_to_the_device_range(self) -> None:
        self.assertEqual(infer_temp("空调调到32度", SKILLS), [("ac", {"value": 30})])
        self.assertEqual(infer_temp("空调调到10度", SKILLS), [("ac", {"value": 16})])
        self.assertEqual(infer_temp("空调调到24度", SKILLS), [("ac", {"value": 24})])


class ReplyParsingTest(unittest.TestCase):
    def test_model_action_lines(self) -> None:
        raw = "好。\nACTION: desk-led.off\nACTION: wegame.run\n"
        from dock_hub.intents import parse_action_lines

        self.assertEqual(parse_action_lines(raw), [("desk-led", False), ("wegame", True)])

    def test_out_of_range_brightness_is_dropped(self) -> None:
        self.assertEqual(parse_bri_actions("ACTION: lamp.bri.100"), [("lamp", {"value": 100})])
        self.assertEqual(parse_bri_actions("ACTION: lamp.bri.250"), [])


class AsrFoldingTest(unittest.TestCase):
    def test_misheard_game_name(self) -> None:
        self.assertIn("无畏契约", fold_game_asr("打开五位契约"))

    def test_misheard_ledstrip(self) -> None:
        self.assertIn("灯带", fold_game_asr("关掉灯戴"))

    def test_lamp_is_not_turned_into_ledstrip(self) -> None:
        self.assertIn("台灯", fold_game_asr("关掉台灯"))


class SkillTest(unittest.TestCase):
    def test_resolve_by_nickname(self) -> None:
        self.assertEqual(resolve_skill("打瓦", SKILLS), "wegame")
        self.assertEqual(resolve_skill("灯带", SKILLS), "desk-led")
        self.assertIsNone(resolve_skill("微波炉", SKILLS))

    def test_prompt_tells_the_model_the_action_syntax(self) -> None:
        text = skill_prompt(SKILLS)
        self.assertIn("ACTION: <id>.on", text)
        self.assertIn("灯带（desk-led）", text)

    def test_query_kind_routes_questions(self) -> None:
        self.assertEqual(query_kind("现在几点"), "time")
        self.assertEqual(query_kind("台灯开着吗"), "light")
        self.assertEqual(query_kind("在播什么歌"), "media")
        self.assertIsNone(query_kind("晚安"))


class TeachTest(unittest.TestCase):
    def test_learn_a_phrase(self) -> None:
        got = parse_teach("以后我说开黑就启动无畏契约", SKILLS)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["phrase"], "开黑")
        self.assertEqual(got["id"], "wegame")
        self.assertTrue(got["on"])

    def test_unknown_target_is_rejected(self) -> None:
        self.assertIsNone(parse_teach("以后我说开黑就启动微波炉", SKILLS))

    def test_forbidden_phrase_is_rejected(self) -> None:
        self.assertIsNone(parse_teach("以后我说打开就启动无畏契约", SKILLS))

    def test_normalize_strips_punctuation(self) -> None:
        self.assertEqual(normalize_phrase("「开黑。」"), "开黑")


class MediaAndVolumeTest(unittest.TestCase):
    """媒体 / 音量：和开关灯同一套写法，只是目标不是设备。"""

    def test_media_words(self) -> None:
        self.assertEqual(infer_media("下一首"), "next")
        self.assertEqual(infer_media("上一首"), "previous")
        self.assertEqual(infer_media("继续放"), "toggle")
        self.assertEqual(infer_media("暂停"), "toggle")

    def test_pause_the_lamp_is_not_pause_the_music(self) -> None:
        # 「灯光停一下」这类话里有「停」，但不能当成暂停播放
        self.assertIsNone(infer_media("灯先停一下"))

    def test_media_action_line(self) -> None:
        self.assertEqual(parse_media_action("ACTION: media.next"), "next")
        self.assertIsNone(parse_media_action("ACTION: media.nope"))

    def test_volume_words(self) -> None:
        self.assertEqual(infer_volume("声音大一点"), "up")
        self.assertEqual(infer_volume("小声点"), "down")
        self.assertEqual(infer_volume("静音"), "mute")
        # 取消静音而不是再按一次静音
        self.assertEqual(infer_volume("取消静音"), "up")

    def test_volume_action_line(self) -> None:
        self.assertEqual(parse_volume_action("ACTION: volume.down"), "down")
        self.assertIsNone(parse_volume_action("ACTION: volume.sideways"))


class TimerTest(unittest.TestCase):
    def test_minutes_and_hours(self) -> None:
        self.assertEqual(parse_timer("25分钟后提醒我"), 25 * 60)
        self.assertEqual(parse_timer("一小时后叫我"), 60 * 60)
        # 带量词的「一个」不被认：数字和单位之间夹了字，正则不匹配
        self.assertIsNone(parse_timer("一个小时后叫我"))
        self.assertEqual(parse_timer("半小时后喊我一声"), 30 * 60)

    def test_vague_phrase_gets_five_minutes(self) -> None:
        self.assertEqual(parse_timer("过一会儿叫我"), 5 * 60)

    def test_needs_a_reminder_word(self) -> None:
        self.assertIsNone(parse_timer("现在几点了"))

    def test_bounds(self) -> None:
        # 太短没意义，太长（超过两小时）不接受
        self.assertIsNone(parse_timer("5秒后叫我"))
        self.assertIsNone(parse_timer("3个小时后叫我"))

    def test_action_line_wins(self) -> None:
        self.assertEqual(parse_timer_action("ACTION: timer.5"), 300)
        self.assertIsNone(parse_timer_action("ACTION: timer.500"))
        self.assertEqual(parse_timer("ACTION: timer.5"), 300)


class NoiseTest(unittest.TestCase):
    def test_on_phrases(self) -> None:
        self.assertEqual(
            infer_noise("放点雨声"),
            {"action": "on", "name": "rain", "minutes": None},
        )
        self.assertEqual(
            infer_noise("来点海浪"),
            {"action": "on", "name": "waves", "minutes": None},
        )
        self.assertEqual(infer_noise("开一下风扇")["name"], "fan")
        self.assertEqual(infer_noise("听布朗噪音")["name"], "brown")

    def test_white_noise_is_a_category_defaulting_to_rain(self) -> None:
        # 「白噪音」是类目不是具体声音，默认给雨声——它最不会错
        self.assertEqual(infer_noise("听白噪音")["name"], "rain")

    def test_minutes_clamped(self) -> None:
        self.assertEqual(infer_noise("放点雨声 45 分钟")["minutes"], 45)
        self.assertEqual(infer_noise("放点雨声 999 分钟")["minutes"], 180)

    def test_off_phrases(self) -> None:
        self.assertEqual(infer_noise("把白噪音关了"), {"action": "off"})
        self.assertEqual(infer_noise("雨声停一下"), {"action": "off"})
        self.assertEqual(infer_noise("白噪音别放了"), {"action": "off"})

    def test_plain_talk_is_not_noise(self) -> None:
        # 「别放了」没点名是哪种声音，不猜——可能说的是朗读
        self.assertIsNone(infer_noise("别放了"))
        # 聊天里聊到雨，不是要放雨声
        self.assertIsNone(infer_noise("外面下雨了吗"))
        self.assertIsNone(infer_noise("明天有雨"))


class IndependenceTest(unittest.TestCase):
    def test_importing_intents_does_not_drag_in_companion(self) -> None:
        # 这一层要能被别处单独用（以后的场景口头触发），不能反过来依赖
        # companion 的线程、网络和 TTS 队列。
        code = "import sys, dock_hub.intents; print('dock_hub.companion' in sys.modules)"
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

        self.assertIs(companion.infer_control, infer_control)
        self.assertIs(companion.parse_teach, parse_teach)


if __name__ == "__main__":
    unittest.main()
