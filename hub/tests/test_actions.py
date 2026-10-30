"""actions.py：把 ACTION 行翻成设备动作。

这段逻辑以前埋在 Companion 里，验一次「说开灯但模型写了别的设备」要起半个 Hub。
现在技能表、别名、world 回调都从参数进来，用假的 run_skill 就能把
「单台失败不连坐」「用户没说控制类的话就别猜」「亮度按当前值加 delta」跑一遍。
"""

from __future__ import annotations

import unittest

from dock_hub.actions import apply_device_skills, call_skill, launch_apps, set_temperature
from dock_hub.errors import HubError

SKILLS: list[dict[str, str]] = [
    {"id": "lamp", "name": "台灯", "kind": "device"},
    {"id": "desk-led", "name": "灯带", "kind": "device", "brightness": "true"},
    {"id": "ac", "name": "空调", "kind": "device", "temp": "true", "temp_id": "temp"},
    {"id": "remix", "name": "Remix", "kind": "run"},
]


class Recorder:
    """假的 run_skill：记账，按需在某台设备上抛错。"""

    def __init__(self, fail: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, bool, dict[str, object]]] = []
        self.fail = fail or {}

    def __call__(self, ident: str, on: bool, **kwargs: object) -> None:
        if ident in self.fail:
            raise HubError(self.fail[ident], "动不了")
        self.calls.append((ident, on, kwargs))


def run(
    text: str,
    reply: str,
    *,
    skills: list[dict[str, str]] | None = None,
    runner: Recorder | None = None,
    world: dict | None = None,
):
    notes: list[tuple[str, str]] = []
    applied, events = apply_device_skills(
        user_text=text,
        raw_reply=reply,
        turn="t1",
        skills=SKILLS if skills is None else skills,
        aliases={},
        run_skill=runner if runner is not None else Recorder(),
        world=world or {},
        note=lambda turn, stage, **fields: notes.append((turn, stage)),
    )
    return applied, events, notes


class ApplyDeviceSkillsTest(unittest.TestCase):
    def test_action_line_turns_light_on(self) -> None:
        runner = Recorder()
        applied, events, notes = run("把台灯打开", "好。\nACTION: 台灯.on", runner=runner)
        self.assertTrue(applied)
        self.assertEqual(runner.calls, [("lamp", True, {})])
        self.assertEqual(events, [{"id": "lamp", "name": "台灯", "on": True, "ok": True}])
        self.assertIn(("t1", "skill_ok"), notes)

    def test_one_device_failing_does_not_stop_the_rest(self) -> None:
        runner = Recorder(fail={"lamp": "offline"})
        applied, events, _ = run(
            "都打开",
            "ACTION: 台灯.on\nACTION: 灯带.on",
            runner=runner,
        )
        self.assertTrue(applied, "第二台成了就算动了")
        self.assertFalse(events[0]["ok"])
        self.assertEqual(events[0]["code"], "offline")
        self.assertTrue(events[1]["ok"])
        self.assertEqual([call[0] for call in runner.calls], ["desk-led"])

    def test_model_action_ignored_when_user_was_not_asking(self) -> None:
        runner = Recorder()
        applied, events, _ = run("今天好累", "抱抱。\nACTION: 台灯.off", runner=runner)
        self.assertFalse(applied)
        self.assertEqual(events, [])
        self.assertEqual(runner.calls, [])

    def test_brightness_delta_uses_current_value(self) -> None:
        runner = Recorder()
        world = {"device": lambda ident: {"brightness": 60}}
        applied, events, _ = run("灯带亮一点", "好。", runner=runner, world=world)
        self.assertTrue(applied)
        self.assertEqual(runner.calls, [("desk-led", True, {"brightness": 75})])
        self.assertEqual(events[0]["brightness"], 75)

    def test_brightness_clamped_into_range(self) -> None:
        runner = Recorder()
        world = {"device": lambda ident: {"brightness": 95}}
        run("灯带亮一点", "好。", runner=runner, world=world)
        self.assertEqual(runner.calls[0][2]["brightness"], 100)

    def test_brightness_without_state_starts_from_half(self) -> None:
        runner = Recorder()
        applied, _events, _ = run("灯带暗一点", "好。", runner=runner)
        self.assertTrue(applied)
        self.assertEqual(runner.calls[0][2]["brightness"], 35)

    def test_ledstrip_mention_does_not_touch_the_lamp(self) -> None:
        runner = Recorder()
        run("把灯带关了", "ACTION: 台灯.off\nACTION: 灯带.off", runner=runner)
        self.assertEqual([call[0] for call in runner.calls], ["desk-led"])

    def test_no_skills_is_a_no_op(self) -> None:
        applied, events, _ = run("开灯", "ACTION: 台灯.on", skills=[])
        self.assertFalse(applied)
        self.assertEqual(events, [])


class TemperatureTest(unittest.TestCase):
    def test_aircon_temperature_goes_through_props(self) -> None:
        runner = Recorder()
        notes: list[tuple[str, str]] = []
        spoken, events = set_temperature(
            text="空调调到二十六度",
            turn="t1",
            skills=SKILLS,
            run_skill=runner,
            note=lambda turn, stage, **fields: notes.append((turn, stage)),
        )
        self.assertEqual(spoken, "好，空调二十六度。")
        self.assertEqual(runner.calls, [("ac", True, {"props": {"temp": 26}})])
        self.assertTrue(events[0]["ok"])

    def test_out_of_range_temperature_is_clamped(self) -> None:
        runner = Recorder()
        spoken, _events = set_temperature(
            text="空调调到十五度",
            turn="t1",
            skills=SKILLS,
            run_skill=runner,
            note=lambda *a, **k: None,
        )
        self.assertEqual(spoken, "好，空调十六度。")

    def test_without_aircon_it_stays_quiet(self) -> None:
        spoken = set_temperature(
            text="空调调到二十六度",
            turn="t1",
            skills=[{"id": "lamp", "name": "台灯", "kind": "device"}],
            run_skill=Recorder(),
            note=lambda *a, **k: None,
        )
        self.assertIsNone(spoken)


class LaunchTest(unittest.TestCase):
    def test_run_skill_kind_is_launched(self) -> None:
        runner = Recorder()
        spoken, events = launch_apps(
            text="打开 Remix",
            turn="t1",
            skills=SKILLS,
            aliases={},
            run_skill=runner,
            note=lambda *a, **k: None,
        )
        self.assertEqual(spoken, "好，开Remix。")
        self.assertEqual(runner.calls, [("remix", True, {})])
        self.assertTrue(events[0]["ok"])

    def test_device_skill_is_not_launched_as_program(self) -> None:
        spoken = launch_apps(
            text="打开台灯",
            turn="t1",
            skills=SKILLS,
            aliases={},
            run_skill=Recorder(),
            note=lambda *a, **k: None,
        )
        self.assertIsNone(spoken)

    def test_unknown_program_is_not_guessed(self) -> None:
        spoken = launch_apps(
            text="打开 Photoshop",
            turn="t1",
            skills=SKILLS,
            aliases={},
            run_skill=Recorder(),
            note=lambda *a, **k: None,
        )
        self.assertIsNone(spoken)


class CallSkillTest(unittest.TestCase):
    def test_old_two_argument_runner_still_works(self) -> None:
        seen: list[tuple[str, bool]] = []

        def old(ident: str, on: bool) -> None:
            seen.append((ident, on))

        call_skill(old, "lamp", True, brightness=40)
        self.assertEqual(seen, [("lamp", True)])

    def test_missing_runner_is_silent(self) -> None:
        call_skill(None, "lamp", True)


if __name__ == "__main__":
    unittest.main()
