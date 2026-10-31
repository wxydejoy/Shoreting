from __future__ import annotations

import unittest
from unittest.mock import patch

from dock_hub import companion_desk as module
from dock_hub.companion import Companion
from dock_hub.config import CompanionConfig


class SlowDimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, bool, int | None]] = []
        self.state: dict[str, dict] = {
            "lamp": {"on": True, "brightness": 90},
            "desk-led": {"on": False, "brightness": 0},
            "plug": {"on": True},
        }
        self.comp = Companion(CompanionConfig(enabled=True))
        self.comp.set_skills(
            [
                {"id": "lamp", "name": "台灯", "kind": "toggle", "brightness": True},
                {"id": "desk-led", "name": "灯带", "kind": "toggle", "brightness": True},
                {"id": "plug", "name": "插座", "kind": "toggle"},
            ],
            self._run,
            None,
            world={"device": lambda ident: self.state.get(ident)},
        )
        for name, value in (("SLOW_DIM_SEC", 0.12), ("SLOW_DIM_STEPS", 3)):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, ident: str, on: bool, brightness: int | None = None, props=None) -> None:
        self.calls.append((ident, on, brightness))

    def _join(self) -> None:
        thread = self.comp._dim_thread  # noqa: SLF001
        if thread is not None:
            thread.join(timeout=3)

    def test_plain_off_is_not_slow(self) -> None:
        self.assertIsNone(self.comp._handle_desk("关掉台灯", "t1"))  # noqa: SLF001
        self.assertEqual(self.calls, [])

    def test_slow_off_ramps_down_then_switches_off(self) -> None:
        result = self.comp._handle_desk("慢慢关灯", "t2")  # noqa: SLF001
        self.assertIsNotNone(result)
        spoken, events = result
        self.assertEqual(spoken, "好，灯慢慢暗。")
        self.assertEqual([item["id"] for item in events], ["lamp"])
        self.assertTrue(events[0]["slow"])
        self._join()
        levels = [level for ident, _on, level in self.calls if ident == "lamp" and level]
        self.assertEqual(levels, sorted(levels, reverse=True))
        self.assertLess(levels[-1], 90)
        self.assertEqual(self.calls[-1], ("lamp", False, None))

    def test_off_lights_are_skipped(self) -> None:
        self.state["lamp"] = {"on": False, "brightness": 0}
        self.assertIsNone(self.comp._handle_desk("慢慢关灯", "t3"))  # noqa: SLF001
        self.assertEqual(self.calls, [])

    def test_named_light_ignores_the_others(self) -> None:
        self.state["desk-led"] = {"on": True, "brightness": 70}
        result = self.comp._handle_desk("灯带慢慢关", "t4")  # noqa: SLF001
        self.assertIsNotNone(result)
        self._join()
        self.assertEqual({ident for ident, _on, _lvl in self.calls}, {"desk-led"})

    def test_non_dimmable_switch_just_turns_off(self) -> None:
        result = self.comp._handle_desk("插座慢慢关掉", "t5")  # noqa: SLF001
        self.assertIsNotNone(result)
        spoken, events = result
        self.assertEqual(spoken, "好，灯慢慢暗。")
        self.assertNotIn("slow", events[0])
        self.assertEqual(self.calls, [("plug", False, None)])

    def test_stop_cancels_the_ramp(self) -> None:
        self.comp._handle_desk("慢慢关灯", "t6")  # noqa: SLF001
        self.comp.stop()
        self._join()
        self.assertNotIn(("lamp", False, None), self.calls)

    def test_second_request_restarts_the_ramp(self) -> None:
        self.comp._handle_desk("慢慢关灯", "t7")  # noqa: SLF001
        first = self.comp._dim_thread  # noqa: SLF001
        self.comp._handle_desk("慢慢关灯", "t8")  # noqa: SLF001
        first.join(timeout=3)
        self._join()
        gen = self.comp._dim_gen  # noqa: SLF001
        self.assertEqual(gen, 2)
        levels = [level for ident, _on, level in self.calls if ident == "lamp" and level]
        self.assertTrue(levels)
