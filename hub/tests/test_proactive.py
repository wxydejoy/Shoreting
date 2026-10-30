"""主动播报：到点开口之前要过的每一道闸。"""

import unittest
from datetime import datetime, timedelta

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.proactive import MORNING_WINDOW, Proactive, in_quiet_hours


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 30, hour, minute)


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.allow = True

    def __call__(self, text: str) -> bool:
        if not self.allow:
            return False
        self.said.append(text)
        return True


def make(config=None, speaker=None, compose=None) -> tuple[Proactive, FakeSpeaker]:
    from dock_hub.config import ProactiveConfig

    speaker = speaker or FakeSpeaker()
    return (
        Proactive(config or ProactiveConfig(enabled=True), speak=speaker, compose_morning=compose),
        speaker,
    )


class TestQuietHours(unittest.TestCase):
    def test_normal_range(self) -> None:
        self.assertTrue(in_quiet_hours(("23:00", "07:00"), at(23, 30)))
        self.assertTrue(in_quiet_hours(("23:00", "07:00"), at(3, 0)))
        self.assertTrue(in_quiet_hours(("23:00", "07:00"), at(6, 59)))
        self.assertFalse(in_quiet_hours(("23:00", "07:00"), at(7, 0)))
        self.assertFalse(in_quiet_hours(("23:00", "07:00"), at(12, 0)))
        self.assertFalse(in_quiet_hours(("23:00", "07:00"), at(22, 59)))

    def test_no_overnight(self) -> None:
        self.assertTrue(in_quiet_hours(("13:00", "14:00"), at(13, 30)))
        self.assertFalse(in_quiet_hours(("13:00", "14:00"), at(2, 0)))

    def test_same_clock_disables(self) -> None:
        self.assertFalse(in_quiet_hours(("07:00", "07:00"), at(7, 0)))


class TestMorning(unittest.TestCase):
    def _proactive(self, speaker=None, text=""):
        from dock_hub.config import MorningConfig, ProactiveConfig

        return Proactive(
            ProactiveConfig(enabled=True, morning=MorningConfig(at="07:30", text=text)),
            speak=speaker or FakeSpeaker(),
            compose_morning=lambda now: f"早。{now.hour}点。",
        )

    def test_before_time_stays_silent(self) -> None:
        bot = self._proactive()
        self.assertEqual(bot.tick(at(7, 0)).reason, "idle")

    def test_says_once_and_only_once(self) -> None:
        speaker = FakeSpeaker()
        bot = self._proactive(speaker)
        first = bot.tick(at(7, 30))
        self.assertTrue(first.spoke)
        self.assertEqual(first.text, "早。7点。")
        # 同一天再怎么到点也不重复
        self.assertEqual(bot.tick(at(7, 35)).reason, "idle")
        self.assertEqual(bot.tick(at(9, 0)).reason, "idle")
        self.assertEqual(len(speaker.said), 1)

    def test_late_within_window_still_says(self) -> None:
        bot = self._proactive()
        late = at(7, 30) + MORNING_WINDOW - timedelta(minutes=1)
        self.assertTrue(bot.tick(late).spoke)

    def test_beyond_window_skips_the_day(self) -> None:
        bot = self._proactive()
        late = at(7, 30) + MORNING_WINDOW + timedelta(minutes=5)
        self.assertEqual(bot.tick(late).reason, "idle")
        # 而且这一天不会再补说（窗口过了就是过了）
        self.assertEqual(bot.tick(at(11, 0)).reason, "idle")

    def test_configured_text_wins(self) -> None:
        speaker = FakeSpeaker()
        bot = self._proactive(speaker, text="早，记得喝水。")
        said = bot.tick(at(8, 0))
        self.assertEqual(said.text, "早，记得喝水。")
        self.assertEqual(speaker.said, ["早，记得喝水。"])

    def test_next_day_again(self) -> None:
        bot = self._proactive()
        self.assertTrue(bot.tick(at(7, 40)).spoke)
        next_day = datetime(2026, 10, 31, 7, 40)
        self.assertTrue(bot.tick(next_day).spoke)


class TestGates(unittest.TestCase):
    def test_disabled(self) -> None:
        from dock_hub.config import ProactiveConfig

        bot, _ = make(ProactiveConfig())
        self.assertEqual(bot.tick(at(8, 0)).reason, "off")

    def test_quiet_beats_due(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        bot, _ = make(
            ProactiveConfig(
                enabled=True, morning=MorningConfig(at="07:30"), quiet=("00:00", "23:59")
            )
        )
        self.assertEqual(bot.tick(at(8, 0)).reason, "quiet")

    def test_quota_beats_due(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        bot, _ = make(
            ProactiveConfig(enabled=True, daily_limit=1, morning=MorningConfig(at="07:30"))
        )
        bot._day = at(7, 45).date()  # 不然 tick 会当成新的一天把配额清零
        bot._count = 1  # 今天已经主动说过一次
        self.assertEqual(bot.tick(at(7, 45)).reason, "quota")

    def test_blocked_does_not_burn_quota(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        speaker = FakeSpeaker()
        speaker.allow = False
        bot = Proactive(
            ProactiveConfig(enabled=True, morning=MorningConfig(at="07:30")),
            speak=speaker,
        )
        self.assertEqual(bot.tick(at(7, 45)).reason, "blocked")
        speaker.allow = True
        said = bot.tick(at(7, 50))
        self.assertTrue(said.spoke, "被拦下不算说过，回来还要说")


class TestConfigure(unittest.TestCase):
    def test_configure_keeps_daily_count(self) -> None:
        from dock_hub.config import MorningConfig, ProactiveConfig

        speaker = FakeSpeaker()
        bot = Proactive(
            ProactiveConfig(enabled=True, daily_limit=1, morning=MorningConfig(at="07:30")),
            speak=speaker,
        )
        self.assertTrue(bot.tick(at(7, 40)).spoke)
        bot.configure(ProactiveConfig(enabled=True, daily_limit=1))
        self.assertEqual(bot.tick(at(9, 0)).reason, "quota", "改配置不重置当日配额")


class TestConfigParse(unittest.TestCase):
    def test_parse_and_roundtrip(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "proactive": {
                    "daily_limit": 3,
                    "quiet": "23:00-07:30",
                    "morning": {"at": "7:30", "text": "早。"},
                },
                "devices": [],
            }
        )
        self.assertTrue(cfg.proactive.enabled)
        self.assertEqual(cfg.proactive.daily_limit, 3)
        self.assertEqual(cfg.proactive.quiet, ("23:00", "07:30"))
        self.assertEqual(cfg.proactive.morning.at, "07:30")
        self.assertEqual(cfg.proactive.morning.text, "早。")
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.proactive, cfg.proactive)

    def test_off_by_default(self) -> None:
        cfg = parse_config({"name": "study", "token": "secret-token-value", "devices": []})
        self.assertFalse(cfg.proactive.enabled)
        self.assertEqual(cfg.proactive.quiet, ("23:00", "07:00"))
        # 关着就不进 round-trip 的 raw，别把没配的东西写回文件
        self.assertNotIn("proactive", hub_config_to_raw(cfg))

    def test_bad_clock_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "proactive": {"morning": {"at": "七点半"}},
                    "devices": [],
                }
            )

    def test_proactive_false(self) -> None:
        cfg = parse_config(
            {"name": "study", "token": "secret-token-value", "proactive": False, "devices": []}
        )
        self.assertFalse(cfg.proactive.enabled)
        self.assertEqual(cfg.proactive.daily_limit, 6)


class TestSpeakUp(unittest.TestCase):
    """companion.speak_up：手机不在、在打游戏就说不了，也不留痕。"""

    def _companion(self):
        from dock_hub.companion import Companion
        from dock_hub.config import CompanionConfig

        bot = Companion(CompanionConfig(enabled=True))
        calls = {"finish": 0}

        def finish(spoken, turn, seq, started, **kw):
            calls["finish"] += 1
            return {"text": spoken, "audio_id": None}

        bot._finish_spoken = finish  # type: ignore[method-assign]
        bot._commit_reply = lambda text: None  # type: ignore[method-assign]
        bot._stop_tts = lambda: None  # type: ignore[method-assign]
        return bot, calls

    def test_says_when_clear(self) -> None:
        bot, calls = self._companion()
        bot._unsolicited_block = lambda: None  # type: ignore[method-assign]
        self.assertTrue(bot.speak_up("早。今天也慢慢来。"))
        self.assertEqual(calls["finish"], 1)

    def test_blocked_by_gate(self) -> None:
        bot, calls = self._companion()
        bot._unsolicited_block = lambda: "disconnected"  # type: ignore[method-assign]
        self.assertFalse(bot.speak_up("早。"))
        self.assertEqual(calls["finish"], 0, "被闸拦下不能走到出声那一步")

    def test_disabled_companion(self) -> None:
        from dock_hub.companion import Companion
        from dock_hub.config import CompanionConfig

        bot = Companion(CompanionConfig(enabled=False))
        self.assertFalse(bot.speak_up("早。"))

    def test_blank_text_is_no(self) -> None:
        bot, _ = self._companion()
        bot._unsolicited_block = lambda: None  # type: ignore[method-assign]
        self.assertFalse(bot.speak_up("   "))


if __name__ == "__main__":
    unittest.main()
